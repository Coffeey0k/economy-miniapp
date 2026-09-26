import db_compat as sqlite3
from datetime import datetime
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import config
import database as db
from keyboards import (
    admin_menu, admin_catalog_menu, cancel_button,
    admin_promos_menu, admin_promo_type_menu, admin_promo_list_menu, admin_promo_actions,
    admin_wheel_menu, admin_wheel_sector_actions, admin_cursed_egg_menu
)

router = Router()


class AdminStates(StatesGroup):
    # Деньги
    waiting_give_user = State()
    waiting_give_amount = State()
    waiting_take_user = State()
    waiting_take_amount = State()
    # Баланс / бан
    waiting_balance_user = State()
    waiting_ban_user = State()
    # Покупки
    waiting_remove_user = State()
    # Рассылка
    waiting_broadcast_text = State()
    # Массовый бонус
    waiting_mass_bonus = State()
    # Промокоды
    waiting_promo_code = State()
    waiting_promo_value = State()
    waiting_promo_maxuses = State()
    waiting_promo_expires = State()
    waiting_promo_edit_value = State()
    waiting_promo_edit_maxuses = State()
    waiting_promo_edit_expires = State()
    # Колесо
    waiting_wheel_chance = State()
    waiting_wheel_label = State()
    waiting_wheel_value = State()
    # Проклятое яйцо
    # Каталог
    waiting_item_type = State()
    waiting_item_name = State()
    waiting_item_price = State()
    waiting_item_desc = State()
    waiting_edit_item = State()
    waiting_edit_price = State()
    waiting_delete_item_name = State()
    waiting_edit_value = State()
    waiting_delete_item = State()
    waiting_edit_value = State()
# ==================== ГЛАВНАЯ АДМИНКА ====================

@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "👑 Админ-панель\n\nВыберите действие:",
        reply_markup=admin_menu(),
        parse_mode=None
    )
    await callback.answer()


# ==================== ВЫДАТЬ МОНЕТЫ ====================

@router.callback_query(F.data == "admin_give")
async def admin_give_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "💰 Выдача ParadiseCoin\n\nВведите username (без @) или ID пользователя:",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_give_user)
    await callback.answer()


@router.message(AdminStates.waiting_give_user)
async def admin_give_user(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = _find_user(target)
    if not user:
        await message.answer("❌ Пользователь не найден, попробуйте снова:")
        return
    await state.update_data(target_id=user["user_id"], target_name=user["username"] or user["user_id"])
    await message.answer(
        f"Введите количество ParadiseCoin для выдачи @{user['username'] or user['user_id']}:",
        reply_markup=cancel_button()
    )
    await state.set_state(AdminStates.waiting_give_amount)


@router.message(AdminStates.waiting_give_amount)
async def admin_give_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        data = await state.get_data()
        target_id = data["target_id"]
        target_name = data["target_name"]
        if db.update_balance(target_id, amount):
            await message.answer(
                f"✅ Выдано {amount} 🪙 @{target_name}\n"
                f"Новый баланс: {db.get_balance(target_id)} 🪙",
                reply_markup=admin_menu()
            )
        else:
            await message.answer("❌ Ошибка при выдаче")
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число")


# ==================== ЗАБРАТЬ МОНЕТЫ ====================

@router.callback_query(F.data == "admin_take")
async def admin_take_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "💸 Снятие ParadiseCoin\n\nВведите username (без @) или ID пользователя:",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_take_user)
    await callback.answer()


@router.message(AdminStates.waiting_take_user)
async def admin_take_user(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = _find_user(target)
    if not user:
        await message.answer("❌ Пользователь не найден, попробуйте снова:")
        return
    await state.update_data(target_id=user["user_id"], target_name=user["username"] or user["user_id"])
    await message.answer(
        f"Введите количество ParadiseCoin для снятия у @{user['username'] or user['user_id']}:",
        reply_markup=cancel_button()
    )
    await state.set_state(AdminStates.waiting_take_amount)


@router.message(AdminStates.waiting_take_amount)
async def admin_take_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        data = await state.get_data()
        target_id = data["target_id"]
        target_name = data["target_name"]
        if db.update_balance(target_id, -amount):
            await message.answer(
                f"✅ Снято {amount} 🪙 у @{target_name}\n"
                f"Новый баланс: {db.get_balance(target_id)} 🪙",
                reply_markup=admin_menu()
            )
        else:
            await message.answer("❌ Недостаточно монет у пользователя")
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число")


# ==================== БАЛАНС ПОЛЬЗОВАТЕЛЯ ====================

@router.callback_query(F.data == "admin_balance")
async def admin_balance_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🔎 Баланс пользователя\n\nВведите username (без @) или ID:",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_balance_user)
    await callback.answer()


@router.message(AdminStates.waiting_balance_user)
async def admin_balance_show(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = _find_user(target)
    if not user:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    await message.answer(
        f"👤 @{user['username'] or user['user_id']}\n"
        f"🪙 Баланс: {user['balance']} ParadiseCoin",
        reply_markup=admin_menu()
    )
    await state.clear()


# ==================== ВСЕ ПОЛЬЗОВАТЕЛИ ====================

@router.callback_query(F.data == "admin_users")
async def admin_users(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    users = db.get_all_users()
    if not users:
        await callback.message.edit_text("👥 Пользователей пока нет.", reply_markup=admin_menu())
        await callback.answer()
        return
    text = "👥 Все пользователи:\n\n"
    for u in users[:30]:
        status = "🚫" if u["is_banned"] else "✅"
        text += f"{status} @{u['username'] or u['user_id']} — {u['balance']} 🪙\n"
    if len(users) > 30:
        text += f"\n... и ещё {len(users) - 30}"
    await callback.message.edit_text(text, reply_markup=admin_menu(), parse_mode=None)
    await callback.answer()


# ==================== СТАТИСТИКА ====================

@router.callback_query(F.data == "admin_stats")
async def admin_stats(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    s = db.get_statistics()
    await callback.message.edit_text(
        f"📊 Статистика бота\n\n"
        f"👥 Пользователей: {s['total_users']}\n"
        f"🪙 Монет в обращении: {s['total_balance']}\n"
        f"🛍️ Покупок: {s['total_purchases']}",
        reply_markup=admin_menu(),
        parse_mode=None
    )
    await callback.answer()


# ==================== БАН ====================

@router.callback_query(F.data == "admin_ban")
async def admin_ban_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🚫 Блокировка пользователя\n\nВведите username (без @) или ID:",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_ban_user)
    await callback.answer()


@router.message(AdminStates.waiting_ban_user)
async def admin_ban_user(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = _find_user(target)
    if not user:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    new_state = not user["is_banned"]
    db.set_user_ban(user["user_id"], new_state)
    await message.answer(
        f"{'✅ Разблокирован' if not new_state else '🚫 Заблокирован'} @{user['username'] or user['user_id']}",
        reply_markup=admin_menu()
    )
    await state.clear()


# ==================== КАТАЛОГ (заглушки) ====================

@router.callback_query(F.data == "admin_catalog")
async def admin_catalog(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🛍️ Управление каталогом",
        reply_markup=admin_catalog_menu(),
        parse_mode=None
    )
    await callback.answer()



@router.callback_query(F.data == "admin_add_item")
async def admin_add_item(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "➕ Добавление товара\n\nВведите технический ID товара (латиницей, например my_item):",
        reply_markup=cancel_button()
    )
    await state.set_state(AdminStates.waiting_item_type)
    await callback.answer()


@router.message(AdminStates.waiting_item_type)
async def admin_item_type(message: Message, state: FSMContext):
    await state.update_data(item_type=message.text.strip().lower().replace(" ", "_"))
    await message.answer("Введите название товара (с эмодзи):")
    await state.set_state(AdminStates.waiting_item_name)


@router.message(AdminStates.waiting_item_name)
async def admin_item_name(message: Message, state: FSMContext):
    await state.update_data(item_name=message.text.strip())
    await message.answer("Введите цену в монетах (целое число):")
    await state.set_state(AdminStates.waiting_item_price)


@router.message(AdminStates.waiting_item_price)
async def admin_item_price(message: Message, state: FSMContext):
    try:
        price = int(message.text)
    except ValueError:
        await message.answer("❌ Введите число")
        return
    await state.update_data(item_price=price)
    await message.answer("Введите описание (или напишите 'нет'):")
    await state.set_state(AdminStates.waiting_item_desc)


@router.message(AdminStates.waiting_item_desc)
async def admin_item_desc(message: Message, state: FSMContext):
    desc = message.text.strip()
    if desc.lower() == "нет":
        desc = ""
    data = await state.get_data()
    ok = db.add_catalog_item(
        data["item_type"], data["item_name"],
        data["item_price"], desc
    )
    if ok:
        await message.answer(
            f"✅ Товар '{data['item_name']}' добавлен!",
            reply_markup=admin_catalog_menu()
        )
    else:
        await message.answer("❌ Товар с таким ID уже существует", reply_markup=admin_catalog_menu())
    await state.clear()


@router.callback_query(F.data == "admin_edit_price")
async def admin_edit_price_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    items = db.get_all_catalog_items()
    text = "✏️ Изменение товара\n\nТекущие товары:\n"
    for it in items:
        text += f"• {it['item_type']} — {it['name']} ({it['price']:,} 🪙)\n"
    text += "\nВведите технический ID товара для изменения:"
    await callback.message.edit_text(text, reply_markup=cancel_button())
    await state.set_state(AdminStates.waiting_edit_item)
    await callback.answer()


@router.message(AdminStates.waiting_edit_item)
async def admin_edit_item(message: Message, state: FSMContext):
    item_type = message.text.strip()
    item = db.get_price(item_type)
    if not item:
        await message.answer("❌ Товар не найден")
        return
    await state.update_data(edit_item=item_type)
    await message.answer(
        "Что изменить?\n"
        "Напишите одно из:\n"
        "name — название\n"
        "price — цена\n"
        "desc — описание"
    )
    await state.set_state(AdminStates.waiting_edit_price)


@router.message(AdminStates.waiting_edit_price)
async def admin_edit_price_value(message: Message, state: FSMContext):
    field = message.text.strip().lower()
    if field not in ["name", "price", "desc"]:
        await message.answer("❌ Введите: name, price или desc")
        return
    await state.update_data(edit_field=field)
    await message.answer("Введите новое значение:")
    await state.set_state(AdminStates.waiting_edit_value)   # было waiting_delete_item_name


@router.message(AdminStates.waiting_edit_value)
async def admin_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("edit_field")
    item_type = data.get("edit_item")
    val = message.text.strip()
    kwargs = {}
    if field == "name":
        kwargs["name"] = val
    elif field == "price":
        try:
            kwargs["price"] = int(val)
        except ValueError:
            await message.answer("❌ Введите число")
            return
    elif field == "desc":
        kwargs["description"] = val
    db.update_catalog_item(item_type, **kwargs)
    await message.answer("✅ Товар обновлён!", reply_markup=admin_catalog_menu())
    await state.clear()


@router.message(AdminStates.waiting_delete_item_name)
async def admin_edit_value(message: Message, state: FSMContext):
    data = await state.get_data()
    field = data.get("edit_field")
    item_type = data.get("edit_item")
    val = message.text.strip()
    kwargs = {}
    if field == "name":
        kwargs["name"] = val
    elif field == "price":
        try:
            kwargs["price"] = int(val)
        except ValueError:
            await message.answer("❌ Введите число")
            return
    elif field == "desc":
        kwargs["description"] = val
    db.update_catalog_item(item_type, **kwargs)
    await message.answer("✅ Товар обновлён!", reply_markup=admin_catalog_menu())
    await state.clear()


@router.callback_query(F.data == "admin_delete_item")
async def admin_delete_item_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    items = db.get_all_catalog_items()
    text = "❌ Удаление товара\n\nТекущие товары:\n"
    for it in items:
        text += f"• {it['item_type']} — {it['name']}\n"
    text += "\nВведите технический ID товара для удаления:"
    await callback.message.edit_text(text, reply_markup=cancel_button())
    await state.set_state(AdminStates.waiting_delete_item)
    await callback.answer()


@router.message(AdminStates.waiting_delete_item)
async def admin_delete_item_confirm(message: Message, state: FSMContext):
    item_type = message.text.strip().lower().replace(" ", "_")
    if db.delete_catalog_item(item_type):
        await message.answer(
            f"✅ Товар '{item_type}' удалён!",
            reply_markup=admin_catalog_menu()
        )
    else:
        await message.answer(
            "❌ Товар с таким ID не найден. Проверьте список выше.",
            reply_markup=admin_catalog_menu()
        )
    await state.clear()

# ==================== АННУЛИРОВАТЬ ПОКУПКУ ====================

@router.callback_query(F.data == "admin_remove_purchase")
async def admin_remove_purchase_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🗑️ Аннулирование покупки\n\nВведите username (без @) или ID:",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_remove_user)
    await callback.answer()


@router.message(AdminStates.waiting_remove_user)
async def admin_remove_user(message: Message, state: FSMContext):
    target = message.text.strip().lstrip("@")
    user = _find_user(target)
    if not user:
        await message.answer("❌ Пользователь не найден")
        await state.clear()
        return
    purchases = db.get_purchases_full(user["user_id"])
    if not purchases:
        await message.answer("❌ У пользователя нет покупок", reply_markup=admin_menu())
        await state.clear()
        return
    builder = InlineKeyboardBuilder()
    item_names = {
        "change_role": "Смена роли", "anti_warn": "Антиварн",
        "immunity": "Иммунитет", "video": "Видео с вами",
        "unban": "Разбан", "admin": "Админка"
    }
    for pid, itype, is_used in purchases:
        status = "✅" if is_used else "❌"
        builder.row(InlineKeyboardButton(
            text=f"{status} {item_names.get(itype, itype)}",
            callback_data=f"remove_purchase_{pid}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel"))
    await message.answer(
        f"Выберите покупку для аннулирования у @{user['username'] or user['user_id']}:",
        reply_markup=builder.as_markup()
    )
    await state.clear()


@router.callback_query(F.data.startswith("remove_purchase_"))
async def admin_remove_purchase(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    pid = int(callback.data.split("_")[2])
    db.delete_purchase(pid)
    await callback.answer("✅ Покупка аннулирована", show_alert=True)
    await callback.message.edit_text("🗑️ Покупка удалена.", reply_markup=admin_menu())


# ==================== УДАЛИТЬ ИЗ НЬЮ ====================

@router.callback_query(F.data == "admin_show_new_list")
async def admin_show_new_list(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    users = db.get_all_new_members()
    if not users:
        await callback.message.edit_text("📋 Список 'Нью' пуст", reply_markup=admin_menu(), parse_mode=None)
        await callback.answer()
        return
    text = "📋 Список новых участников:\n\n"
    builder = InlineKeyboardBuilder()
    for uid, uname, joined in users:
        display = f"@{uname}" if uname else f"ID:{uid}"
        date = joined[:16] if joined else "?"
        text += f"• {display} — {date}\n"
        builder.row(InlineKeyboardButton(
            text=f"❌ Удалить {display}",
            callback_data=f"admin_delete_new_{uid}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode=None)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_new_"))
async def admin_delete_new(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    uid = int(callback.data.split("_")[3])
    if db.delete_new_member(uid):
        await callback.answer("✅ Удалён из нью", show_alert=True)
        await admin_show_new_list(callback)
    else:
        await callback.answer("❌ Не найден", show_alert=True)


# ==================== МАССОВАЯ РАССЫЛКА ====================

@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "📢 Массовая рассылка\n\n"
        "Напишите текст, который получат все пользователи в ЛС и он будет отправлен в чат.",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_broadcast_text)
    await callback.answer()


@router.message(AdminStates.waiting_broadcast_text)
async def admin_broadcast_send(message: Message, state: FSMContext):
    from aiogram import Bot
    text = message.text
    bot: Bot = message.bot
    users = db.get_all_users()
    sent = 0
    failed = 0
    for u in users:
        if u["is_banned"]:
            continue
        try:
            await bot.send_message(u["user_id"], f"📢 {text}")
            sent += 1
        except Exception:
            failed += 1
    # В чат флуда
    try:
        await bot.send_message(config.FLUD_CHAT_ID, f"📢 {text}")
    except Exception:
        pass
    await message.answer(
        f"✅ Рассылка завершена!\nДоставлено: {sent}\nЗаблокировали бота: {failed}",
        reply_markup=admin_menu()
    )
    await state.clear()


# ==================== МАССОВЫЙ БОНУС ====================

@router.callback_query(F.data == "admin_mass_bonus")
async def admin_mass_bonus_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "💰 Массовый бонус\n\nВведите сумму, которую получат все активные игроки:",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_mass_bonus)
    await callback.answer()


@router.message(AdminStates.waiting_mass_bonus)
async def admin_mass_bonus_send(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
        bot = message.bot
        users = db.get_all_users()
        count = 0
        for u in users:
            if u["is_banned"]:
                continue
            db.update_balance(u["user_id"], amount)
            count += 1
            try:
                await bot.send_message(
                    u["user_id"],
                    f"🎁 Вам начислен массовый бонус: +{amount} 🪙!"
                )
            except Exception:
                pass
        await message.answer(
            f"✅ Массовый бонус {amount} 🪙 выдан {count} игрокам.",
            reply_markup=admin_menu()
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите корректное число")


# ==================== ПРОМОКОДЫ ====================

@router.callback_query(F.data == "admin_promos")
async def admin_promos(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🎟️ Управление промокодами",
        reply_markup=admin_promos_menu(),
        parse_mode=None
    )
    await callback.answer()


@router.callback_query(F.data == "admin_promo_create")
async def admin_promo_create(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "🎟️ Создание промокода\n\nВведите сам код (например, PARADISE2026):",
        reply_markup=cancel_button(),
        parse_mode=None
    )
    await state.set_state(AdminStates.waiting_promo_code)
    await callback.answer()


@router.message(AdminStates.waiting_promo_code)
async def admin_promo_code(message: Message, state: FSMContext):
    code = message.text.strip().upper()
    await state.update_data(code=code)
    await message.answer(
        "Выберите тип награды:",
        reply_markup=admin_promo_type_menu()
    )


@router.callback_query(F.data.startswith("promo_type_"))
async def admin_promo_type(callback: CallbackQuery, state: FSMContext):
    rtype = callback.data.replace("promo_type_", "")
    await state.update_data(reward_type=rtype)
    prompts = {
        "money": "Введите сумму монет:",
        "pet": "Введите название питомца (например, 🐹 Хомячок):",
        "egg": "Введите тип яйца (ordinary/sea/tropical/mystic/legendary/divine):"
    }
    await callback.message.edit_text(prompts.get(rtype, "Введите значение:"))
    await state.set_state(AdminStates.waiting_promo_value)
    await callback.answer()


@router.message(AdminStates.waiting_promo_value)
async def admin_promo_value(message: Message, state: FSMContext):
    await state.update_data(reward_value=message.text.strip())
    await message.answer("Введите максимальное количество активаций (целое число):")
    await state.set_state(AdminStates.waiting_promo_maxuses)


@router.message(AdminStates.waiting_promo_maxuses)
async def admin_promo_maxuses(message: Message, state: FSMContext):
    try:
        max_uses = int(message.text)
        await state.update_data(max_uses=max_uses)
        await message.answer(
            "Введите дату окончания в формате ГГГГ-ММ-ДД ЧЧ:ММ (или напишите 'нет'):"
        )
        await state.set_state(AdminStates.waiting_promo_expires)
    except ValueError:
        await message.answer("❌ Введите корректное число")


@router.message(AdminStates.waiting_promo_expires)
async def admin_promo_expires(message: Message, state: FSMContext):
    expires = message.text.strip()
    if expires.lower() == "нет":
        expires = None
    else:
        try:
            datetime.strptime(expires, "%Y-%m-%d %H:%M")
        except ValueError:
            await message.answer("❌ Неверный формат. Пример: 2026-12-31 23:59")
            return
    data = await state.get_data()
    ok = db.create_promo(
        data["code"], data["reward_type"], data["reward_value"],
        data["max_uses"], expires
    )
    if ok:
        await message.answer(
            f"✅ Промокод {data['code']} создан!\n"
            f"Тип: {data['reward_type']}\n"
            f"Значение: {data['reward_value']}\n"
            f"Активаций: {data['max_uses']}",
            reply_markup=admin_promos_menu()
        )
    else:
        await message.answer("❌ Промокод с таким кодом уже существует", reply_markup=admin_promos_menu())
    await state.clear()


@router.callback_query(F.data == "admin_promo_list")
async def admin_promo_list(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    promos = db.get_all_promos()
    if not promos:
        await callback.message.edit_text(
            "🎟️ Промокодов пока нет.",
            reply_markup=admin_promos_menu(),
            parse_mode=None
        )
        await callback.answer()
        return
    await callback.message.edit_text(
        "🎟️ Список промокодов:",
        reply_markup=admin_promo_list_menu(promos),
        parse_mode=None
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promo_info_"))
async def admin_promo_info(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    code = callback.data.replace("admin_promo_info_", "")
    promo = db.get_promo(code)
    if not promo:
        await callback.answer("❌ Промокод не найден", show_alert=True)
        return
    text = (
        f"🎟️ Промокод: {promo['code']}\n"
        f"Тип: {promo['reward_type']}\n"
        f"Значение: {promo['reward_value']}\n"
        f"Использован: {promo['used_count']}/{promo['max_uses']}\n"
        f"Истекает: {promo['expires_at'] or 'бессрочно'}"
    )
    await callback.message.edit_text(text, reply_markup=admin_promo_actions(code), parse_mode=None)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_promo_delete_"))
async def admin_promo_delete(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    code = callback.data.replace("admin_promo_delete_", "")
    db.delete_promo(code)
    await callback.answer("✅ Промокод удалён", show_alert=True)
    await admin_promo_list(callback)


@router.callback_query(F.data.startswith("admin_promo_edit_"))
async def admin_promo_edit(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    code = callback.data.replace("admin_promo_edit_", "")
    await state.update_data(edit_code=code)
    await callback.message.edit_text(
        "Введите новое значение награды (или напишите 'нет', чтобы оставить):"
    )
    await state.set_state(AdminStates.waiting_promo_edit_value)
    await callback.answer()


@router.message(AdminStates.waiting_promo_edit_value)
async def admin_promo_edit_value(message: Message, state: FSMContext):
    val = message.text.strip()
    if val.lower() != "нет":
        data = await state.get_data()
        db.update_promo(data["edit_code"], reward_value=val)
    await message.answer("Введите новое число активаций (или 'нет'):")
    await state.set_state(AdminStates.waiting_promo_edit_maxuses)


@router.message(AdminStates.waiting_promo_edit_maxuses)
async def admin_promo_edit_maxuses(message: Message, state: FSMContext):
    val = message.text.strip()
    if val.lower() != "нет":
        try:
            db.update_promo((await state.get_data())["edit_code"], max_uses=int(val))
        except ValueError:
            pass
    await message.answer("Введите новую дату окончания ГГГГ-ММ-ДД ЧЧ:ММ (или 'нет'):")
    await state.set_state(AdminStates.waiting_promo_edit_expires)


@router.message(AdminStates.waiting_promo_edit_expires)
async def admin_promo_edit_expires(message: Message, state: FSMContext):
    val = message.text.strip()
    if val.lower() != "нет":
        try:
            datetime.strptime(val, "%Y-%m-%d %H:%M")
            db.update_promo((await state.get_data())["edit_code"], expires_at=val)
        except ValueError:
            await message.answer("❌ Неверный формат")
            await state.clear()
            return
    await message.answer("✅ Промокод обновлён!", reply_markup=admin_promos_menu())
    await state.clear()


# ==================== КОЛЕСО ФОРТУНЫ (АДМИН) ====================

@router.callback_query(F.data == "admin_wheel")
async def admin_wheel(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    sectors = db.get_wheel_sectors()
    await callback.message.edit_text(
        "🎡 Настройки колеса фортуны:",
        reply_markup=admin_wheel_menu(sectors),
        parse_mode=None
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_wheel_sector_"))
async def admin_wheel_sector(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    sid = int(callback.data.split("_")[3])
    await callback.message.edit_text(
        "Что сделать с делением?",
        reply_markup=admin_wheel_sector_actions(sid),
        parse_mode=None
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_wheel_delete_"))
async def admin_wheel_delete(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    sid = int(callback.data.split("_")[3])
    db.delete_wheel_sector(sid)
    await callback.answer("✅ Удалено", show_alert=True)
    await admin_wheel(callback)


@router.callback_query(F.data == "admin_wheel_add")
async def admin_wheel_add(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    await callback.message.edit_text(
        "Введите название нового деления (например, '500 монет' или 'Питомец Хомячок'):"
    )
    await state.set_state(AdminStates.waiting_wheel_label)
    await callback.answer()


@router.message(AdminStates.waiting_wheel_label)
async def admin_wheel_label(message: Message, state: FSMContext):
    await state.update_data(label=message.text.strip())
    await message.answer(
        "Выберите тип награды:\n"
        "money — монеты\n"
        "pet — питомец\n"
        "egg — яйцо\n"
        "random_purchase — случайная покупка\n\n"
        "Напишите одно из этих слов:"
    )
    await state.set_state(AdminStates.waiting_wheel_value)


@router.message(AdminStates.waiting_wheel_value)
async def admin_wheel_value(message: Message, state: FSMContext):
    val = message.text.strip()
    if val not in ["money", "pet", "egg", "random_purchase"]:
        await message.answer("❌ Неверный тип")
        return
    await state.update_data(reward_type=val)
    if val == "money":
        await message.answer("Введите сумму монет:")
    elif val == "pet":
        await message.answer("Введите имя питомца (например, 🐹 Хомячок):")
    elif val == "egg":
        await message.answer("Введите тип яйца (ordinary/sea/tropical/mystic/legendary/divine):")
    else:
        await state.update_data(reward_value="")
        await message.answer("Введите шанс выпадения (в %):")
        await state.set_state(AdminStates.waiting_wheel_chance)
        return
    await state.set_state(AdminStates.waiting_wheel_chance)


@router.message(AdminStates.waiting_wheel_chance)
async def admin_wheel_chance(message: Message, state: FSMContext):
    # Если reward_value ещё не установлено — это значение награды
    data = await state.get_data()
    text = message.text.strip()
    if "reward_value" not in data or data.get("reward_value") is None:
        await state.update_data(reward_value=text)
        await message.answer("Введите шанс выпадения (в %):")
        return
    # Это шанс
    try:
        chance = float(text.replace(",", "."))
    except ValueError:
        await message.answer("❌ Введите число")
        return
    db.add_wheel_sector(
        data["label"], data["reward_type"], data["reward_value"], chance
    )
    await message.answer("✅ Деление добавлено!", reply_markup=admin_menu())
    await state.clear()


# ==================== ПРОКЛЯТОЕ ЯЙЦО ====================

@router.callback_query(F.data == "admin_cursed_egg")
async def admin_cursed_egg(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    stock = db.get_egg_stock()
    text = (
        f"🥚 Проклятое яйцо\n\n"
        f"Статус: {'🟢 Активно' if stock['active'] else '🔴 Неактивно'}\n"
        f"Осталось: {stock['stock']} шт."
    )
    await callback.message.edit_text(text, reply_markup=admin_cursed_egg_menu(), parse_mode=None)
    await callback.answer()


@router.callback_query(F.data == "admin_cursed_activate")
async def admin_cursed_activate(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    db.set_cursed_egg_active(5)
    await _announce_cursed_egg(callback.bot)
    await callback.answer("✅ Проклятое яйцо активировано", show_alert=True)
    await admin_cursed_egg(callback)


@router.callback_query(F.data == "admin_cursed_deactivate")
async def admin_cursed_deactivate(callback: CallbackQuery):
    if callback.from_user.id not in config.ADMIN_IDS:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return
    db.deactivate_cursed_egg()
    await callback.answer("✅ Деактивировано", show_alert=True)
    await admin_cursed_egg(callback)


# ==================== ОТМЕНА ====================

@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено.", reply_markup=admin_menu())
    await callback.answer()


# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================

def _find_user(query: str):
    """Ищет пользователя по username или ID"""
    conn = sqlite3.connect(db.DB_NAME)
    cur = conn.cursor()
    if query.isdigit():
        cur.execute("SELECT user_id, username, balance, is_banned FROM users WHERE user_id = ?", (int(query),))
    else:
        cur.execute("SELECT user_id, username, balance, is_banned FROM users WHERE username = ?", (query,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"user_id": row[0], "username": row[1], "balance": row[2], "is_banned": bool(row[3])}
    return None

async def _announce_cursed_egg(bot):
    """Оповещение о появлении проклятого яйца"""
    stock = db.get_egg_stock()
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
    for u in db.get_all_users():
        try:
            await bot.send_message(u["user_id"], text)
        except Exception:
            pass
    try:
        await bot.send_message(config.FLUD_CHAT_ID, text)
    except Exception:
        pass
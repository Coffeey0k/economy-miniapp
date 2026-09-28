"""
Косметика ParadiseCoin.

Магазин косметики, покупка и применение:
- Цвет ника
- Рамки
- Статусы
- Темы (для веба)
"""

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database as db

router = Router()


# ==================== ГЛАВНОЕ МЕНЮ КОСМЕТИКИ ====================

def cosmetics_menu() -> InlineKeyboardBuilder:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎨 Цвет ника", callback_data="cos_color"))
    builder.row(InlineKeyboardButton(text="🖼️ Рамки", callback_data="cos_frame"))
    builder.row(InlineKeyboardButton(text="🏷️ Статусы", callback_data="cos_status"))
    builder.row(InlineKeyboardButton(text="🌈 Темы", callback_data="cos_theme"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder


@router.callback_query(F.data == "cosmetics_menu")
async def cosmetics_main(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎨 Косметика\n\nВыбери категорию:",
        reply_markup=cosmetics_menu().as_markup()
    )
    await callback.answer()


@router.message(F.text.lower() == "косметика")
async def cosmetics_word(message: Message):
    await message.answer(
        "🎨 Косметика\n\nВыбери категорию:",
        reply_markup=cosmetics_menu().as_markup()
    )


# ==================== ЦВЕТ НИКА ====================

@router.callback_query(F.data == "cos_color")
async def cos_color(callback: CallbackQuery):
    user_id = callback.from_user.id
    cosmetics = db.get_cosmetics(user_id)
    current = cosmetics["nickname_color"]

    builder = InlineKeyboardBuilder()
    # Каждый цвет — отдельной строкой
    for key, info in db.NICK_COLORS.items():
        mark = "✅ " if key == current else ""
        builder.row(InlineKeyboardButton(
            text=f"{mark}{info['name']}",
            callback_data=f"cos_buy_color_{key}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="cosmetics_menu"))

    await callback.message.edit_text(
        f"🎨 Цвет ника\n\n"
        f"💰 Цена: {db.NICK_COLOR_PRICE:,} 🪙\n"
        f"Текущий: {db.NICK_COLORS[current]['name'] if current else 'не установлен'}\n\n"
        f"Выбери цвет:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cos_buy_color_"))
async def cos_buy_color(callback: CallbackQuery):
    color_key = callback.data.replace("cos_buy_color_", "")
    user_id = callback.from_user.id

    cosmetics = db.get_cosmetics(user_id)
    if cosmetics["nickname_color"] == color_key:
        await callback.answer("❌ Этот цвет уже установлен", show_alert=True)
        return

    ok, msg = db.purchase_cosmetics(user_id, "color", color_key)
    if ok:
        await callback.answer(f"✅ {msg}", show_alert=True)
        await cos_color(callback)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)


# ==================== РАМКИ ====================

@router.callback_query(F.data == "cos_frame")
async def cos_frame(callback: CallbackQuery):
    user_id = callback.from_user.id
    cosmetics = db.get_cosmetics(user_id)
    current = cosmetics["frame"]

    builder = InlineKeyboardBuilder()
    for key, info in db.FRAMES.items():
        mark = "✅ " if key == current else ""
        builder.row(InlineKeyboardButton(
            text=f"{mark}{info['display'].format(name='@you')} — {info['price']:,} 🪙",
            callback_data=f"cos_buy_frame_{key}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="cosmetics_menu"))

    await callback.message.edit_text(
        f"🖼️ Рамки\n\n"
        f"Текущая: {db.FRAMES[current]['display'].format(name='@you') if current else 'нет'}\n\n"
        f"Выбери рамку:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cos_buy_frame_"))
async def cos_buy_frame(callback: CallbackQuery):
    frame_key = callback.data.replace("cos_buy_frame_", "")
    user_id = callback.from_user.id

    cosmetics = db.get_cosmetics(user_id)
    if cosmetics["frame"] == frame_key:
        await callback.answer("❌ Эта рамка уже установлена", show_alert=True)
        return

    ok, msg = db.purchase_cosmetics(user_id, "frame", frame_key)
    if ok:
        await callback.answer(f"✅ {msg}", show_alert=True)
        await cos_frame(callback)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)


# ==================== СТАТУСЫ ====================

@router.callback_query(F.data == "cos_status")
async def cos_status(callback: CallbackQuery):
    user_id = callback.from_user.id
    cosmetics = db.get_cosmetics(user_id)
    current = cosmetics["status"]

    builder = InlineKeyboardBuilder()
    for key, info in db.STATUSES.items():
        mark = "✅ " if key == current else ""
        builder.row(InlineKeyboardButton(
            text=f"{mark}{info['name']} — {info['price']:,} 🪙",
            callback_data=f"cos_buy_status_{key}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="cosmetics_menu"))

    current_text = db.STATUSES[current]['name'] if current else 'нет'
    await callback.message.edit_text(
        f"🏷️ Статусы\n\n"
        f"Текущий: {current_text}\n\n"
        f"Выбери статус:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cos_buy_status_"))
async def cos_buy_status(callback: CallbackQuery):
    status_key = callback.data.replace("cos_buy_status_", "")
    user_id = callback.from_user.id

    cosmetics = db.get_cosmetics(user_id)
    if cosmetics["status"] == status_key:
        await callback.answer("❌ Этот статус уже установлен", show_alert=True)
        return

    ok, msg = db.purchase_cosmetics(user_id, "status", status_key)
    if ok:
        await callback.answer(f"✅ {msg}", show_alert=True)
        await cos_status(callback)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)


# ==================== ТЕМЫ ====================

@router.callback_query(F.data == "cos_theme")
async def cos_theme(callback: CallbackQuery):
    user_id = callback.from_user.id
    cosmetics = db.get_cosmetics(user_id)
    current = cosmetics["theme"]

    builder = InlineKeyboardBuilder()
    for key, info in db.THEMES.items():
        mark = "✅ " if key == current else ""
        price = f"{info['price']:,} 🪙" if info['price'] > 0 else "бесплатно"
        builder.row(InlineKeyboardButton(
            text=f"{mark}{info['name']} — {price}",
            callback_data=f"cos_buy_theme_{key}"
        ))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="cosmetics_menu"))

    await callback.message.edit_text(
        f"🌈 Темы\n\n"
        f"Текущая: {db.THEMES[current]['name']}\n"
        f"⚠️ Темы применяются в веб-версии.\n\n"
        f"Выбери тему:",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("cos_buy_theme_"))
async def cos_buy_theme(callback: CallbackQuery):
    theme_key = callback.data.replace("cos_buy_theme_", "")
    user_id = callback.from_user.id

    cosmetics = db.get_cosmetics(user_id)
    if cosmetics["theme"] == theme_key:
        await callback.answer("❌ Эта тема уже установлена", show_alert=True)
        return

    # Бесплатные темы — просто применяем
    if db.THEMES[theme_key]["price"] == 0:
        db.set_cosmetics(user_id, theme=theme_key)
        await callback.answer("✅ Тема применена", show_alert=True)
        await cos_theme(callback)
        return

    ok, msg = db.purchase_cosmetics(user_id, "theme", theme_key)
    if ok:
        await callback.answer(f"✅ {msg}", show_alert=True)
        await cos_theme(callback)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ============ ГЛАВНОЕ МЕНЮ ============

def main_menu(user_id: int = None) -> InlineKeyboardMarkup:
    """Главное меню"""
    import config
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🏠 Главная", callback_data="main_home"),
        InlineKeyboardButton(text="🎮 Игры", callback_data="games_menu")
    )
    builder.row(
        InlineKeyboardButton(text="📦 Каталог", callback_data="catalog"),
        InlineKeyboardButton(text="💸 Переводы", callback_data="transfer_menu")
    )
    builder.row(
        InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
        InlineKeyboardButton(text="🎁 Бонус", callback_data="claim_bonus")
    )
    builder.row(
        InlineKeyboardButton(text="🐾 Питомцы", callback_data="pets_menu"),
        InlineKeyboardButton(text="💼 Профессии", callback_data="professions_menu")
    )
    builder.row(
        InlineKeyboardButton(text="📋 Задания", callback_data="tasks_menu"),
        InlineKeyboardButton(text="🎡 Колесо", callback_data="wheel_menu")
    )
    if user_id and user_id in config.ADMIN_IDS:
        builder.row(
            InlineKeyboardButton(text="👑 Админ-панель", callback_data="admin_panel")
        )
    return builder.as_markup()


def home_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔄 Обновить топ", callback_data="refresh_top"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def games_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎰 Слот", callback_data="game_slot"),
        InlineKeyboardButton(text="🎲 Кубик", callback_data="game_dice")
    )
    builder.row(
        InlineKeyboardButton(text="🪙 Монетка", callback_data="game_coin"),
        InlineKeyboardButton(text="🎯 Дартс", callback_data="game_dart")
    )
    builder.row(InlineKeyboardButton(text="🃏 Угадай число", callback_data="game_number"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def catalog_menu() -> InlineKeyboardMarkup:
    from database import get_all_catalog_items
    builder = InlineKeyboardBuilder()
    items = get_all_catalog_items()
    row = []
    for it in items:
        row.append(InlineKeyboardButton(
            text=f"{it['name']} — {it['price']:,} 🪙",
            callback_data=f"buy_{it['item_type']}"
        ))
        if len(row) == 2:
            builder.row(*row)
            row = []
    if row:
        builder.row(*row)
    builder.row(InlineKeyboardButton(text="🥚 Яйца питомцев", callback_data="eggs_menu"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def eggs_menu() -> InlineKeyboardMarkup:
    from database import get_egg_stock
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🥚 Обычное яйцо — 1,000 🪙", callback_data="egg_ordinary"))
    builder.row(InlineKeyboardButton(text="🌊 Морское яйцо — 30,000 🪙", callback_data="egg_sea"))
    builder.row(InlineKeyboardButton(text="🌴 Тропическое яйцо — 60,000 🪙", callback_data="egg_tropical"))
    builder.row(InlineKeyboardButton(text="🌙 Мистическое яйцо — 150,000 🪙", callback_data="egg_mystic"))
    builder.row(InlineKeyboardButton(text="💎 Легендарное яйцо — 500,000 🪙", callback_data="egg_legendary"))
    builder.row(InlineKeyboardButton(text="👑 Божественное яйцо — 1,000,000 🪙", callback_data="egg_divine"))
    
    # Проклятое яйцо — только если активно
    stock = get_egg_stock()
    if stock["active"] and stock["stock"] > 0:
        builder.row(InlineKeyboardButton(
            text=f"🕯️ Проклятое яйцо — 10,000 🪙 (осталось: {stock['stock']})",
            callback_data="open_cursed"
        ))
    
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="catalog"))
    return builder.as_markup()


def transfer_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💸 Перевести монеты", callback_data="transfer_start"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def game_bet_menu(game_type: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for amount in [10, 50, 100, 500, 1000]:
        builder.row(
            InlineKeyboardButton(text=f"{amount} 🪙", callback_data=f"bet_{game_type}_{amount}")
        )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="games_menu"))
    return builder.as_markup()


def cancel_button() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


# ============ ПИТОМЦЫ ============

def pets_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🐾 Мои питомцы", callback_data="my_pets"))
    builder.row(InlineKeyboardButton(text="🥚 Купить яйцо", callback_data="eggs_menu"))
    builder.row(InlineKeyboardButton(text="🎁 Подарить питомца", callback_data="gift_pet_start"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def my_pets_menu(pets: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for pet in pets:
        status = "✅" if pet["is_active"] else "💤"
        builder.row(
            InlineKeyboardButton(
                text=f"{status} {pet['name']} ({pet['rarity']}, {pet['income']}🪙/4ч)",
                callback_data=f"pet_info_{pet['id']}"
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="pets_menu"))
    return builder.as_markup()


def pet_actions_menu(pet_id: int, is_active: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if is_active:
        builder.row(InlineKeyboardButton(text="💤 Деактивировать", callback_data=f"pet_deactivate_{pet_id}"))
    else:
        builder.row(InlineKeyboardButton(text="✅ Активировать", callback_data=f"pet_activate_{pet_id}"))
    builder.row(InlineKeyboardButton(text="🎁 Подарить", callback_data=f"pet_gift_{pet_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="my_pets"))
    return builder.as_markup()


# ============ ПРОФЕССИИ ============

def professions_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💼 Моя профессия", callback_data="my_profession"))
    builder.row(InlineKeyboardButton(text="📋 Список профессий", callback_data="list_professions"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def professions_list_menu(user_id: int = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    from database import PROFESSIONS
    import config
    for key, info in PROFESSIONS.items():
        if info["admin_only"] and (user_id not in config.ADMIN_IDS and user_id != config.OWNER_ID):
            continue
        builder.row(
            InlineKeyboardButton(
                text=f"{info['name']} — {info['salary']}🪙/{info['cooldown_hours']}ч",
                callback_data=f"hire_{key}"
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="professions_menu"))
    return builder.as_markup()

# ============ ЗАДАНИЯ ============

def tasks_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="📋 Мои задания", callback_data="my_tasks"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


# ============ КОЛЕСО ФОРТУНЫ ============

def wheel_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🎡 Крутить (200 🪙)", callback_data="wheel_spin"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


# ============ ТУТОРИАЛ ============

def tutorial_nav_menu(step: int, total: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if step < total:
        builder.row(InlineKeyboardButton(text="➡️ Далее", callback_data=f"tutorial_next_{step + 1}"))
    else:
        builder.row(InlineKeyboardButton(text="✅ Завершить", callback_data="tutorial_finish"))
    builder.row(InlineKeyboardButton(text="⏭️ Пропустить", callback_data="tutorial_skip"))
    return builder.as_markup()


# ============ ПОДСКАЗКИ ============

def hints_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отключить подсказки", callback_data="hints_disable"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


# ============ ПРОМОКОДЫ ============

def promo_cancel_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()


# ============ АДМИН-ПАНЕЛЬ ============

def admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="💰 Выдать монеты", callback_data="admin_give"),
        InlineKeyboardButton(text="💸 Забрать монеты", callback_data="admin_take")
    )
    builder.row(
        InlineKeyboardButton(text="🔎 Баланс", callback_data="admin_balance"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")
    )
    builder.row(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="🚫 Бан", callback_data="admin_ban")
    )
    builder.row(
        InlineKeyboardButton(text="🛍️ Каталог", callback_data="admin_catalog"),
        InlineKeyboardButton(text="🗑️ Аннулировать покупку", callback_data="admin_remove_purchase")
    )
    builder.row(
        InlineKeyboardButton(text="🗑️ Удалить из нью", callback_data="admin_show_new_list"),
        InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")
    )
    builder.row(
        InlineKeyboardButton(text="💰 Массовый бонус", callback_data="admin_mass_bonus"),
        InlineKeyboardButton(text="🎟️ Промокоды", callback_data="admin_promos")
    )
    builder.row(
        InlineKeyboardButton(text="🎡 Колесо (настройки)", callback_data="admin_wheel"),
        InlineKeyboardButton(text="🥚 Проклятое яйцо", callback_data="admin_cursed_egg")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return builder.as_markup()


def admin_catalog_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_item"),
        InlineKeyboardButton(text="✏️ Изменить цену", callback_data="admin_edit_price")
    )
    builder.row(InlineKeyboardButton(text="❌ Удалить товар", callback_data="admin_delete_item"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    return builder.as_markup()


# ============ АДМИН: ПРОМОКОДЫ ============

def admin_promos_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="➕ Создать промокод", callback_data="admin_promo_create"))
    builder.row(InlineKeyboardButton(text="📋 Список промокодов", callback_data="admin_promo_list"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    return builder.as_markup()


def admin_promo_type_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="💰 Монеты", callback_data="promo_type_money"))
    builder.row(InlineKeyboardButton(text="🐾 Питомец", callback_data="promo_type_pet"))
    builder.row(InlineKeyboardButton(text="🥚 Яйцо", callback_data="promo_type_egg"))
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="admin_promos"))
    return builder.as_markup()


def admin_promo_list_menu(promos: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for p in promos:
        builder.row(
            InlineKeyboardButton(
                text=f"🎟️ {p['code']} ({p['used_count']}/{p['max_uses']})",
                callback_data=f"admin_promo_info_{p['code']}"
            )
        )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promos"))
    return builder.as_markup()


def admin_promo_actions(code: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin_promo_edit_{code}"))
    builder.row(InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_promo_delete_{code}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_promo_list"))
    return builder.as_markup()


# ============ АДМИН: КОЛЕСО ============

def admin_wheel_menu(sectors: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for s in sectors:
        builder.row(
            InlineKeyboardButton(
                text=f"🎡 {s['label']} — {s['chance']}%",
                callback_data=f"admin_wheel_sector_{s['id']}"
            )
        )
    builder.row(InlineKeyboardButton(text="➕ Добавить деление", callback_data="admin_wheel_add"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    return builder.as_markup()


def admin_wheel_sector_actions(sector_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✏️ Изменить", callback_data=f"admin_wheel_edit_{sector_id}"))
    builder.row(InlineKeyboardButton(text="🗑️ Удалить", callback_data=f"admin_wheel_delete_{sector_id}"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_wheel"))
    return builder.as_markup()


# ============ АДМИН: ПРОКЛЯТОЕ ЯЙЦО ============

def admin_cursed_egg_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="✅ Активировать (5 шт)", callback_data="admin_cursed_activate"))
    builder.row(InlineKeyboardButton(text="❌ Деактивировать", callback_data="admin_cursed_deactivate"))
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel"))
    return builder.as_markup()


# ============ ВСПОМОГАТЕЛЬНЫЕ ============

def back_to_main_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main"))
    return builder.as_markup()


def back_to_catalog_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📦 Каталог", callback_data="catalog"),
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main")
    )
    return builder.as_markup()


def confirm_transfer_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_transfer"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_transfer")
    )
    return builder.as_markup()


def coin_choice_keyboard(bet: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🦅 Орел", callback_data=f"coin_choice_Орел_{bet}"),
        InlineKeyboardButton(text="🪙 Решка", callback_data=f"coin_choice_Решка_{bet}")
    )
    builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="games_menu"))
    return builder.as_markup()


def confirm_admin_purchase() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_admin_buy"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="back_to_main")
    )
    return builder.as_markup()


def game_result_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🎮 Играть еще", callback_data="games_menu"),
        InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_to_main")
    )
    return builder.as_markup()
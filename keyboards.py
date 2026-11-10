"""Клавиатуры бота (aiogram 3.x)."""
import math
from typing import Optional

from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                           KeyboardButton, ReplyKeyboardMarkup)

import config
import db
import rates


def main_menu_kb(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🛍 Каталог"), KeyboardButton(text="👤 Профиль")],
        [KeyboardButton(text="💳 Пополнить"), KeyboardButton(text="📋 История")],
    ]
    if config.SUPPORT_URL:
        rows.append([KeyboardButton(text="💬 Поддержка")])
    if is_admin:
        rows.append([KeyboardButton(text="⚙️ Админка")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


# ---------------------------------------------------------------- каталог

CAT_CAP = 40  # максимум подкатегорий в одном экране


async def build_catalog(parent_id: Optional[int], prod_page: int = 0) -> tuple:
    """Собирает экран каталога для категории (или корня).

    Возвращает (text, keyboard | None, photo_id | None).
    None, None, None — если внутри пусто. photo_id — фото категории, если задано.
    """
    cats = await db.child_categories(parent_id)
    prods = await db.products_in(parent_id) if parent_id is not None else []
    if not cats and not prods:
        return None, None, None

    rows: list[list[InlineKeyboardButton]] = []
    for cat in cats[:CAT_CAP]:
        rows.append([InlineKeyboardButton(
            text=f"📁 {cat['title']}",
            callback_data=f"cat:{cat['id']}",
        )])

    if prods:
        stock_map = await db.products_stock_map(parent_id)
        per_page = config.PAGINATION_LIMIT
        total_pages = max(1, math.ceil(len(prods) / per_page))
        prod_page %= total_pages
        chunk = prods[prod_page * per_page:(prod_page + 1) * per_page]
        for p in chunk:
            stock = stock_map.get(p["id"], 0)
            rows.append([InlineKeyboardButton(
                text=f"📦 {p['title']} · {stock} шт",
                callback_data=f"prod:{p['id']}",
            )])
        if total_pages > 1:
            nav: list[InlineKeyboardButton] = []
            if prod_page > 0:
                nav.append(InlineKeyboardButton(
                    text="⬅️", callback_data=f"prodp:{parent_id}:{prod_page - 1}"))
            nav.append(InlineKeyboardButton(
                text=f"{prod_page + 1}/{total_pages}", callback_data="noop"))
            if prod_page < total_pages - 1:
                nav.append(InlineKeyboardButton(
                    text="➡️", callback_data=f"prodp:{parent_id}:{prod_page + 1}"))
            rows.append(nav)

    back_cd: Optional[str] = None
    photo_id: Optional[str] = None
    if parent_id is None:
        title = "🛒 <b>Каталог</b>"
    else:
        cat = await db.get_category(parent_id)
        title = f"📁 <b>{cat['title']}</b>" if cat else "📁"
        if cat:
            photo_id = cat["photo_id"]
            if cat["parent_id"] is not None:
                back_cd = f"cat:{cat['parent_id']}"
            else:
                back_cd = "menu:catalog"
    if back_cd:
        rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data=back_cd)])

    text = f"{title}\n\n🎯 Выберите категорию или товар:"
    return text, InlineKeyboardMarkup(inline_keyboard=rows), photo_id


def product_kb(product_id: int, cat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить", callback_data=f"buy:{product_id}")],
        [InlineKeyboardButton(text="◀️ К категории", callback_data=f"cat:{cat_id}")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])


def confirm_buy_kb(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить покупку", callback_data=f"buyok:{product_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"prod:{product_id}")],
    ])


# ---------------------------------------------------------------- профиль

def currency_kb(current: str) -> InlineKeyboardMarkup:
    buttons = []
    for code, name in rates.CURRENCY_NAMES.items():
        mark = "✅ " if code == current else ""
        buttons.append([InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"cur:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------- пополнение

def deposit_provider_kb() -> InlineKeyboardMarkup:
    from payments import available_providers
    buttons = []
    for provider in available_providers():
        label = "💎 CryptoBot" if provider == "cryptobot" else "🚀 XRocket"
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"dep:{provider}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def deposit_amount_kb() -> InlineKeyboardMarkup:
    quick = [100, 250, 500, 1000, 2500]  # рубли
    row = [InlineKeyboardButton(text=f"{a} ₽", callback_data=f"depamt:{a}") for a in quick]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def invoice_kb(pay_url: str, invoice_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔗 Перейти к оплате", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"depchk:{invoice_id}")],
        [InlineKeyboardButton(text="🏠 В меню", callback_data="menu:main")],
    ])

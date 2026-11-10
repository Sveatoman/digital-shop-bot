"""Админ-панель: статистика, рассылка, управление категориями/товарами, балансом, пользователями."""
import asyncio
import logging
from contextlib import suppress
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
                           Message)

import config
import db
from payments import provider_label

log = logging.getLogger("admin")

router = Router()


class AdminStates(StatesGroup):
    cat_new_title = State()      # cat_parent в данных
    cat_edit_title = State()     # cat_id в данных
    cat_set_photo = State()      # cat_id в данных
    prod_new_title = State()     # new_cat в данных
    prod_new_desc = State()
    prod_new_price = State()
    prod_new_items = State()
    prod_add_items = State()     # prod_id в данных
    prod_set_photo = State()     # prod_id в данных
    prod_edit_field = State()    # prod_id, field в данных
    broadcast_text = State()
    find_user = State()
    balance_amount = State()     # target_user в данных


# ---------------------------------------------------------------- helpers

def admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm:stats")],
        [InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:bcast"),
         InlineKeyboardButton(text="🏷 Категории", callback_data="adm:cats")],
        [InlineKeyboardButton(text="👥 Пользователь", callback_data="adm:users"),
         InlineKeyboardButton(text="💳 Пополнения", callback_data="adm:deposits")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:main")],
    ])


async def _edit_safe(message: Message, text: str, reply_markup=None) -> None:
    with suppress(Exception):
        await message.edit_text(text, reply_markup=reply_markup)


# ---------------------------------------------------------------- вход

@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not config.is_admin(message.from_user.id):
        await message.answer("⛔️ Недостаточно прав.")
        return
    await message.answer("⚙️ <b>Админ-панель</b>\n\nВыберите раздел:", reply_markup=admin_kb())


@router.callback_query(F.data == "adm:home")
async def cb_admin_home(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await _edit_safe(cb.message, "⚙️ <b>Админ-панель</b>\n\nВыберите раздел:", admin_kb())


# ---------------------------------------------------------------- статистика

@router.callback_query(F.data == "adm:stats")
async def cb_stats(cb: CallbackQuery) -> None:
    await cb.answer()
    conn = db._conn()
    stats = {}
    queries = {
        "users": "SELECT COUNT(*) FROM users",
        "banned": "SELECT COUNT(*) FROM users WHERE banned = 1",
        "cats": "SELECT COUNT(*) FROM categories",
        "prods": "SELECT COUNT(*) FROM products",
        "stock": "SELECT COUNT(*) FROM product_items WHERE sold_at IS NULL",
        "sold_items": "SELECT COUNT(*) FROM product_items WHERE sold_at IS NOT NULL",
        "purchases": "SELECT COUNT(*) FROM purchases",
        "revenue": "SELECT COALESCE(SUM(price), 0) FROM purchases",
    }
    for name, q in queries.items():
        cur = await conn.execute(q)
        stats[name] = (await cur.fetchone())[0]
    dep_stats = await db.deposits_stats()

    text = (
        "📊 <b>Статистика магазина</b>\n\n"
        f"👥 Пользователей: <b>{stats['users']}</b> (🚫 {stats['banned']})\n"
        f"🏷 Категорий: <b>{stats['cats']}</b>\n"
        f"📦 Товаров: <b>{stats['prods']}</b>\n\n"
        f"✅ Позиций в наличии: <b>{stats['stock']}</b>\n"
        f"📈 Продано позиций: <b>{stats['sold_items']}</b>\n"
        f"🛍 Покупок: <b>{stats['purchases']}</b>\n"
        f"💰 Оборот: <code>{stats['revenue']:.2f} ₽</code>\n\n"
        f"💳 Зачислено пополнений: <code>{dep_stats['paid_sum']:.2f} ₽</code>\n"
        f"⏳ Счетов ожидает оплаты: <b>{dep_stats['pending_cnt']}</b>"
    )
    await _edit_safe(cb.message, text, admin_kb())


# ---------------------------------------------------------------- отмена

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    await state.clear()
    if current is None:
        await message.answer("Нечего отменять.")
        return
    await message.answer("❌ Отменено.")
    if config.is_admin(message.from_user.id):
        await message.answer("⚙️ Админ-панель:", reply_markup=admin_kb())


# ---------------------------------------------------------------- рассылка

@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(AdminStates.broadcast_text)
    await cb.message.answer(
        "📣 <b>Рассылка</b>\n\n"
        "Отправьте текст (поддерживается HTML) или фото с подписью.\n"
        "Сообщение будет отправлено всем активным пользователям.\n\n"
        "❌ /cancel — отмена"
    )


@router.message(AdminStates.broadcast_text, F.photo)
async def st_bcast_photo(message: Message, state: FSMContext) -> None:
    if not message.caption:
        await message.answer("❌ Отправьте фото с подписью или просто текст.")
        return
    await _bcast_confirm(message, state, message.caption, message.photo[-1].file_id)


@router.message(AdminStates.broadcast_text)
async def st_bcast_text(message: Message, state: FSMContext) -> None:
    if not message.text:
        await message.answer("❌ Отправьте текст или фото с подписью.")
        return
    await _bcast_confirm(message, state, message.text, None)


async def _bcast_confirm(message: Message, state: FSMContext, text: str, photo_id: Optional[str]) -> None:
    await state.update_data(bc_text=text, bc_photo=photo_id)
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Начать рассылку", callback_data="adm:bcast_go")],
        [InlineKeyboardButton(text="✏️ Изменить", callback_data="adm:bcast")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="adm:home")],
    ])
    if photo_id:
        await message.answer_photo(photo_id, caption=text[:1024])
        await message.answer("👆 Так выглядит сообщение. Запускаем?", reply_markup=confirm_kb)
    else:
        await message.answer(text)
        await message.answer("👆 Так выглядит сообщение. Запускаем?", reply_markup=confirm_kb)


@router.callback_query(F.data == "adm:bcast_go")
async def cb_bcast_go(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer("Запускаю рассылку...")
    data = await state.get_data()
    text = data.get("bc_text")
    photo = data.get("bc_photo")
    if not text:
        await cb.message.answer("❌ Текст не найден. Начните заново: /admin")
        return
    await state.clear()

    user_ids = await db.all_user_ids(only_active=True)
    await _edit_safe(cb.message, f"🚀 Рассылка запущена: {len(user_ids)} получателей...")

    sent = 0
    failed = 0
    for uid in user_ids:
        try:
            if photo:
                await cb.bot.send_photo(uid, photo, caption=text)
            else:
                await cb.bot.send_message(uid, text)
            sent += 1
        except Exception as e:
            failed += 1
            log.debug("broadcast to %s failed: %s", uid, e)
        await asyncio.sleep(0.04)  # ~25 сообщений/сек (оптимизировано)

    await cb.bot.send_message(
        cb.from_user.id,
        f"✅ <b>Рассылка завершена</b>\n\n"
        f"✅ Доставлено: <b>{sent}</b>\n"
        f"❌ Не доставлено: <b>{failed}</b>",
        reply_markup=admin_kb(),
    )


# ---------------------------------------------------------------- категории

async def _root_cats_content() -> tuple:
    cats = await db.child_categories(None, visible_only=False)
    buttons = []
    for cat in cats:
        sub_cnt = await db.count_subcategories(cat["id"])
        prod_cnt = await db.count_products(cat["id"])
        vis = "✅" if cat["is_visible"] else "🚫"
        buttons.append([InlineKeyboardButton(
            text=f"{vis} {cat['title']} · {sub_cnt}кат {prod_cnt}тов",
            callback_data=f"admcat:{cat['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="➕ Новая категория", callback_data="admcatnew:0")])
    buttons.append([InlineKeyboardButton(text="◀️ В админку", callback_data="adm:home")])
    return ("🏷 <b>Категории</b>\n\n"
            "Формат: видимость · название · подкатегорий · товаров.",
            InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data == "adm:cats")
async def cb_cats(cb: CallbackQuery) -> None:
    await cb.answer()
    text, kb = await _root_cats_content()
    await _edit_safe(cb.message, text, kb)


async def _cat_menu_content(cat_id: int) -> tuple:
    cat = await db.get_category(cat_id)
    if not cat:
        return None, None
    sub_cnt = await db.count_subcategories(cat_id)
    prod_cnt = await db.count_products(cat_id)
    photo_row = [InlineKeyboardButton(
        text=("📷 Заменить фото" if cat["photo_id"] else "📷 Добавить фото"),
        callback_data=f"admcatphoto:{cat_id}")]
    if cat["photo_id"]:
        photo_row.append(InlineKeyboardButton(
            text="🗑 Убрать фото", callback_data=f"admcatnophoto:{cat_id}"))
    buttons = [
        [InlineKeyboardButton(text="➕ Подкатегория", callback_data=f"admcatnew:{cat_id}"),
         InlineKeyboardButton(text="➕ Товар", callback_data=f"admprodnew:{cat_id}")],
        [InlineKeyboardButton(text="✏️ Переименовать", callback_data=f"admcatrename:{cat_id}"),
         InlineKeyboardButton(
             text=("👁 Скрыть" if cat["is_visible"] else "👁 Показать"),
             callback_data=f"admcatvis:{cat_id}")],
        photo_row,
    ]
    if sub_cnt:
        buttons.append([InlineKeyboardButton(
            text=f"📂 Подкатегории ({sub_cnt})", callback_data=f"adcatsub:{cat_id}")])
    if prod_cnt:
        buttons.append([InlineKeyboardButton(
            text=f"📦 Товары ({prod_cnt})", callback_data=f"admprodlist:{cat_id}")])
    buttons.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admcatdel:{cat_id}")])
    if cat["parent_id"] is None:
        buttons.append([InlineKeyboardButton(text="⬆️ К списку категорий", callback_data="adm:cats")])
    else:
        buttons.append([InlineKeyboardButton(text="⬆️ К родителю", callback_data=f"admcat:{cat['parent_id']}")])
    text = (
        f"🏷 <b>{cat['title']}</b> <code>#{cat_id}</code>\n\n"
        f"Подкатегорий: <b>{sub_cnt}</b> · Товаров: <b>{prod_cnt}</b>\n"
        f"Фото: {'📷 установлено' if cat['photo_id'] else '— нет'}\n"
        f"Статус: {'✅ видима' if cat['is_visible'] else '🚫 скрыта'}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _show_cat_menu(cb: CallbackQuery, cat_id: int) -> None:
    text, kb = await _cat_menu_content(cat_id)
    if text is None:
        await cb.answer("Категория не найдена", show_alert=True)
        return
    await _edit_safe(cb.message, text, kb)


@router.callback_query(F.data.startswith("admcat:"))
async def cb_cat_manage(cb: CallbackQuery) -> None:
    await cb.answer()
    await _show_cat_menu(cb, int(cb.data.split(":")[1]))


@router.callback_query(F.data.startswith("admcatnew:"))
async def cb_cat_new(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    parent_id = int(cb.data.split(":")[1])
    await state.set_state(AdminStates.cat_new_title)
    await state.update_data(cat_parent=parent_id or None)
    label = "подкатегории" if parent_id else "категории"
    await cb.message.answer(f"Введите название новой {label}:\n\n❌ /cancel — отмена")


@router.message(AdminStates.cat_new_title)
async def st_cat_new_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) > 64:
        await message.answer("❌ Название: 1–64 символа. Попробуйте ещё раз:")
        return
    data = await state.get_data()
    parent = data.get("cat_parent")
    cat_id = await db.create_category(parent, title)
    await state.clear()
    await message.answer(f"✅ Категория «{title}» создана.")
    await message.answer(
        "Что дальше?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить товар", callback_data=f"admprodnew:{cat_id}")],
            [InlineKeyboardButton(text="➕ Добавить подкатегорию", callback_data=f"admcatnew:{cat_id}")],
            [InlineKeyboardButton(text="⚙️ Управление категорией", callback_data=f"admcat:{cat_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("adcatsub:"))
async def cb_cat_sublist(cb: CallbackQuery) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    cats = await db.child_categories(cat_id, visible_only=False)
    buttons = []
    for cat in cats:
        vis = "✅" if cat["is_visible"] else "🚫"
        buttons.append([InlineKeyboardButton(text=f"{vis} {cat['title']}",
                                              callback_data=f"admcat:{cat['id']}")])
    buttons.append([InlineKeyboardButton(text="➕ Новая подкатегория",
                                          callback_data=f"admcatnew:{cat_id}")])
    buttons.append([InlineKeyboardButton(text="⬆️ К категории", callback_data=f"admcat:{cat_id}")])
    await _edit_safe(cb.message, "📂 <b>Подкатегории</b>",
                     InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("admcatrename:"))
async def cb_cat_rename(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    await state.set_state(AdminStates.cat_edit_title)
    await state.update_data(cat_id=cat_id)
    await cb.message.answer("Введите новое название категории:\n\n❌ /cancel — отмена")


@router.message(AdminStates.cat_edit_title)
async def st_cat_rename(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) > 64:
        await message.answer("❌ Название: 1–64 символа. Попробуйте ещё раз:")
        return
    data = await state.get_data()
    await db.update_category(data["cat_id"], title=title)
    await state.clear()
    await message.answer(f"✅ Переименовано в «{title}».", reply_markup=admin_kb())


@router.callback_query(F.data.startswith("admcatvis:"))
async def cb_cat_vis(cb: CallbackQuery) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    if cat:
        await db.update_category(cat_id, is_visible=0 if cat["is_visible"] else 1)
        await _show_cat_menu(cb, cat_id)


@router.callback_query(F.data.startswith("admcatphoto:"))
async def cb_cat_photo(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    if not cat:
        await cb.answer("Категория не найдена", show_alert=True)
        return
    await state.set_state(AdminStates.cat_set_photo)
    await state.update_data(cat_id=cat_id)
    await cb.message.answer(
        f"📷 Отправьте фото для категории «<b>{cat['title']}</b>».\n\n"
        "Так его увидят покупатели в каталоге.\n"
        "❌ /cancel — отмена"
    )


@router.message(AdminStates.cat_set_photo, F.photo)
async def st_cat_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    cat_id = data["cat_id"]
    photo_id = message.photo[-1].file_id
    await db.update_category(cat_id, photo_id=photo_id)
    await state.clear()
    await message.answer("✅ Фото категории обновлено.")
    await message.answer_photo(photo_id, caption="👆 Так выглядит фото.")
    text, kb = await _cat_menu_content(cat_id)
    if text:
        await message.answer(text, reply_markup=kb)


@router.message(AdminStates.cat_set_photo)
async def st_cat_photo_invalid(message: Message) -> None:
    await message.answer("❌ Это не фото. Отправьте изображение или /cancel")


@router.callback_query(F.data.startswith("admcatnophoto:"))
async def cb_cat_no_photo(cb: CallbackQuery) -> None:
    await cb.answer("Убираю фото...")
    cat_id = int(cb.data.split(":")[1])
    await db.update_category(cat_id, photo_id=None)
    await _show_cat_menu(cb, cat_id)


@router.callback_query(F.data.startswith("admcatdel:"))
async def cb_cat_del(cb: CallbackQuery) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    if not cat:
        await cb.answer("Не найдена", show_alert=True)
        return
    sub_cnt = await db.count_subcategories(cat_id)
    prod_cnt = await db.count_products(cat_id)
    await cb.message.answer(
        f"⚠️ Удалить категорию «{cat['title']}»?\n\n"
        f"Будет каскадно удалено: <b>{sub_cnt}</b> подкатегорий, <b>{prod_cnt}</b> товаров "
        "и все позиции склада. Действие необратимо!",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"admcatdelok:{cat_id}")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admcat:{cat_id}")],
        ]),
    )


@router.callback_query(F.data.startswith("admcatdelok:"))
async def cb_cat_del_ok(cb: CallbackQuery) -> None:
    await cb.answer("Удаляю...")
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    await db.delete_category(cat_id)
    await _edit_safe(cb.message, "🗑 Категория удалена.")
    if cat and cat["parent_id"] is not None:
        text, kb = await _cat_menu_content(cat["parent_id"])
        if text:
            await cb.message.answer(text, reply_markup=kb)
    else:
        text, kb = await _root_cats_content()
        await cb.message.answer(text, reply_markup=kb)


# ---------------------------------------------------------------- товары

@router.callback_query(F.data.startswith("admprodnew:"))
async def cb_prod_new(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    await state.set_state(AdminStates.prod_new_title)
    await state.update_data(new_cat=cat_id)
    await cb.message.answer(
        "➕ <b>Новый товар</b>\n\n"
        "Шаг 1/4 — название товара:\n\n❌ /cancel — отмена"
    )


@router.message(AdminStates.prod_new_title)
async def st_prod_title(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title or len(title) > 100:
        await message.answer("❌ Название: 1–100 символов. Попробуйте ещё раз:")
        return
    await state.update_data(new_title=title)
    await state.set_state(AdminStates.prod_new_desc)
    await message.answer("Шаг 2/4 — описание (или «-» чтобы пропустить):")


@router.message(AdminStates.prod_new_desc)
async def st_prod_desc(message: Message, state: FSMContext) -> None:
    desc = (message.text or "").strip()
    await state.update_data(new_desc="" if desc == "-" else desc)
    await state.set_state(AdminStates.prod_new_price)
    await message.answer("Шаг 3/4 — цена в рублях (например: 150):")


@router.message(AdminStates.prod_new_price)
async def st_prod_price(message: Message, state: FSMContext) -> None:
    price = db.parse_amount(message.text)
    if not price:
        await message.answer("❌ Некорректная цена. Введите число, например: 150")
        return
    await state.update_data(new_price=price)
    await state.set_state(AdminStates.prod_new_items)
    await message.answer(
        "Шаг 4/4 — позиции для автовыдачи.\n\n"
        "Каждая позиция = единица товара = одна продажа.\n"
        "Отправьте позиции по одной в строке, либо блоками через пустую строку "
        "(если одна позиция многострочная).\n\n"
        "Пример (3 позиции):\n"
        "<code>login1@mail.com:pass1\nlogin2@mail.com:pass2\nlogin3@mail.com:pass3</code>"
    )


@router.message(AdminStates.prod_new_items)
async def st_prod_items(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Пусто. Отправьте позиции:")
        return
    contents = _split_items(raw)
    if not contents:
        await message.answer("❌ Не распознано. Попробуйте ещё раз:")
        return
    data = await state.get_data()
    product_id = await db.create_product(
        data["new_cat"], data["new_title"], data.get("new_desc", ""), data["new_price"]
    )
    added = await db.add_product_items(product_id, contents)
    await state.clear()
    await message.answer(
        f"✅ Товар «<b>{data['new_title']}</b>» создан!\n\n"
        f"📦 Позиций на складе: <b>{added}</b>\n"
        f"💵 Цена: <code>{data['new_price']:.2f} ₽</code>",
        reply_markup=admin_kb(),
    )


def _split_items(raw: str) -> list[str]:
    """Блоки, разделённые пустой строкой; если блок один — считаем строками."""
    blocks = [b.strip() for b in raw.split("\n\n") if b.strip()]
    if len(blocks) > 1:
        return blocks
    return [ln.strip() for ln in raw.splitlines() if ln.strip()]


@router.callback_query(F.data.startswith("admprodlist:"))
async def cb_prod_list(cb: CallbackQuery) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    prods = await db.products_in(cat_id, visible_only=False)
    if not prods:
        await cb.answer("Товаров нет", show_alert=True)
        return
    stock_map = await db.products_stock_map(cat_id)
    buttons = []
    for p in prods:
        stock = stock_map.get(p["id"], 0)
        vis = "✅" if p["is_visible"] else "🚫"
        buttons.append([InlineKeyboardButton(
            text=f"{vis} {p['title']} · {stock} шт · {p['price']:.2f}",
            callback_data=f"admprod:{p['id']}",
        )])
    buttons.append([InlineKeyboardButton(text="⬆️ К категории", callback_data=f"admcat:{cat_id}")])
    await _edit_safe(cb.message, "📦 <b>Товары</b> (видимость · остаток · цена)",
                     InlineKeyboardMarkup(inline_keyboard=buttons))


async def _prod_menu_content(product_id: int) -> tuple:
    product = await db.get_product(product_id)
    if not product:
        return None, None
    stock = await db.product_stock(product_id)
    conn = db._conn()
    cur = await conn.execute("SELECT COUNT(*) FROM product_items WHERE product_id = ?", (product_id,))
    items_total = (await cur.fetchone())[0]
    photo_row = [InlineKeyboardButton(
        text=("📷 Заменить фото" if product["photo_id"] else "📷 Добавить фото"),
        callback_data=f"admprod:photo:{product_id}")]
    if product["photo_id"]:
        photo_row.append(InlineKeyboardButton(
            text="🗑 Убрать фото", callback_data=f"admprod:nophoto:{product_id}"))
    buttons = [
        [InlineKeyboardButton(text="✏️ Название", callback_data=f"admprod:title:{product_id}"),
         InlineKeyboardButton(text="💵 Цена", callback_data=f"admprod:price:{product_id}")],
        [InlineKeyboardButton(text="📝 Описание", callback_data=f"admprod:desc:{product_id}"),
         InlineKeyboardButton(
             text=("👁 Скрыть" if product["is_visible"] else "👁 Показать"),
             callback_data=f"admprod:vis:{product_id}")],
        photo_row,
        [InlineKeyboardButton(text=f"➕ Добавить позиции (в наличии {stock})",
                              callback_data=f"admprod:additems:{product_id}")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"admprod:del:{product_id}")],
        [InlineKeyboardButton(text="⬆️ К категории", callback_data=f"admcat:{product['category_id']}")],
    ]
    text = (
        f"📦 <b>{product['title']}</b> <code>#{product_id}</code>\n\n"
        f"📝 {product['description'] or '—'}\n\n"
        f"💵 Цена: <code>{product['price']:.2f} ₽</code>\n"
        f"📦 В наличии: <b>{stock}</b> (всего заведено: <b>{items_total}</b>)\n"
        f"Фото: {'📷 установлено' if product['photo_id'] else '— нет'}\n"
        f"Статус: {'✅ виден' if product['is_visible'] else '🚫 скрыт'}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _show_prod_menu(cb: CallbackQuery, product_id: int) -> None:
    text, kb = await _prod_menu_content(product_id)
    if text is None:
        await cb.answer("Товар не найден", show_alert=True)
        return
    await _edit_safe(cb.message, text, kb)


@router.callback_query(F.data.startswith("admprod:"))
@router.callback_query(F.data.startswith("admprodmenu:"))
async def cb_prod_manage(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    parts = cb.data.split(":")
    if parts[0] == "admprodmenu" or len(parts) == 2:
        # admprodmenu:{id} или вход из списка товаров admprod:{id}
        action: str = "menu"
        product_id = int(parts[-1])
    else:
        action, product_id = parts[1], int(parts[2])
    product = await db.get_product(product_id)

    if action == "vis":
        if product:
            await db.update_product(product_id, is_visible=0 if product["is_visible"] else 1)
        await _show_prod_menu(cb, product_id)
        return
    if action == "del":
        if not product:
            await cb.answer("Товар не найден", show_alert=True)
            return
        await cb.message.answer(
            f"⚠️ Удалить товар «{product['title']}» со всеми позициями склада?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"admprod:delok:{product_id}")],
                [InlineKeyboardButton(text="❌ Отмена", callback_data=f"admprodmenu:{product_id}")],
            ]),
        )
        return
    if action == "delok":
        cat_id = product["category_id"] if product else None
        await db.delete_product(product_id)
        await _edit_safe(cb.message, "🗑 Товар удалён.")
        if cat_id:
            text, kb = await _cat_menu_content(cat_id)
            if text:
                await cb.message.answer(text, reply_markup=kb)
        return
    if action == "additems":
        await state.set_state(AdminStates.prod_add_items)
        await state.update_data(prod_id=product_id)
        await cb.message.answer(
            "Отправьте новые позиции (по одной в строке или блоками через пустую строку):"
        )
        return
    if action == "photo":
        if not product:
            await cb.answer("Товар не найден", show_alert=True)
            return
        await state.set_state(AdminStates.prod_set_photo)
        await state.update_data(prod_id=product_id)
        await cb.message.answer(
            f"📷 Отправьте фото для товара «<b>{product['title']}</b>».\n\n"
            "Так его увидят покупатели в каталоге.\n"
            "❌ /cancel — отмена"
        )
        return
    if action == "nophoto":
        if not product:
            await cb.answer("Товар не найден", show_alert=True)
            return
        await db.update_product(product_id, photo_id=None)
        await _show_prod_menu(cb, product_id)
        return
    if action == "price":
        await state.set_state(AdminStates.prod_edit_field)
        await state.update_data(prod_id=product_id, field="price")
        await cb.message.answer("Введите новую цену в рублях:")
        return
    if action == "title":
        await state.set_state(AdminStates.prod_edit_field)
        await state.update_data(prod_id=product_id, field="title")
        await cb.message.answer("Введите новое название:")
        return
    if action == "desc":
        await state.set_state(AdminStates.prod_edit_field)
        await state.update_data(prod_id=product_id, field="desc")
        await cb.message.answer("Введите новое описание (или «-» для пустого):")
        return
    await _show_prod_menu(cb, product_id)


@router.message(AdminStates.prod_set_photo, F.photo)
async def st_prod_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data["prod_id"]
    photo_id = message.photo[-1].file_id
    await db.update_product(product_id, photo_id=photo_id)
    await state.clear()
    await message.answer("✅ Фото товара обновлено.")
    await message.answer_photo(photo_id, caption="👆 Так выглядит фото.")
    text, kb = await _prod_menu_content(product_id)
    if text:
        await message.answer(text, reply_markup=kb)


@router.message(AdminStates.prod_set_photo)
async def st_prod_photo_invalid(message: Message) -> None:
    await message.answer("❌ Это не фото. Отправьте изображение или /cancel")


@router.message(AdminStates.prod_edit_field)
async def st_prod_edit_field(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    product_id = data["prod_id"]
    field = data["field"]
    if field == "price":
        price = db.parse_amount(message.text)
        if not price:
            await message.answer("❌ Некорректная цена. Введите число:")
            return
        await db.update_product(product_id, price=price)
        msg = f"✅ Цена обновлена: <code>{price:.2f} ₽</code>"
    elif field == "title":
        title = (message.text or "").strip()
        if not title:
            await message.answer("❌ Пустое название. Введите снова:")
            return
        await db.update_product(product_id, title=title)
        msg = f"✅ Название обновлено: <b>{title}</b>"
    else:
        desc = (message.text or "").strip()
        await db.update_product(product_id, description="" if desc == "-" else desc)
        msg = "✅ Описание обновлено."
    await state.clear()
    await message.answer(msg, reply_markup=admin_kb())


@router.message(AdminStates.prod_add_items)
async def st_prod_add_items(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("❌ Пусто. Отправьте позиции:")
        return
    contents = _split_items(raw)
    data = await state.get_data()
    added = await db.add_product_items(data["prod_id"], contents)
    stock = await db.product_stock(data["prod_id"])
    await state.clear()
    await message.answer(
        f"✅ Добавлено позиций: <b>{added}</b>\n📦 Теперь на складе: <b>{stock}</b>",
        reply_markup=admin_kb(),
    )


# ---------------------------------------------------------------- пользователи / баланс

@router.callback_query(F.data == "adm:users")
async def cb_users(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(AdminStates.find_user)
    await cb.message.answer(
        "👥 <b>Поиск пользователя</b>\n\n"
        "Отправьте ID или @username:\n\n❌ /cancel — отмена"
    )


@router.message(AdminStates.find_user)
async def st_find_user(message: Message, state: FSMContext) -> None:
    query = (message.text or "").strip().lstrip("@")
    conn = db._conn()
    if query.isdigit():
        cur = await conn.execute("SELECT * FROM users WHERE user_id = ?", (int(query),))
    else:
        cur = await conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (query,))
    row = await cur.fetchone()
    if not row:
        await message.answer("❌ Пользователь не найден. Попробуйте ещё раз или /cancel")
        return
    user = dict(row)
    await state.set_state(AdminStates.balance_amount)
    await state.update_data(target_user=user["user_id"])
    ban_btn = ("🚫 Забанить" if not user["banned"] else "✅ Разбанить")
    await message.answer(
        "👤 <b>Пользователь</b>\n\n"
        f"🆔 <code>{user['user_id']}</code> · @{user['username'] or '—'} · {user['full_name']}\n"
        f"💰 Баланс: <code>{user['balance']:.2f} ₽</code>\n"
        f"🛒 Покупок: <b>{user['purchases_cnt']}</b> на <code>{user['total_spent']:.2f}</code>\n"
        f"⭐️ Статус: {'🚫 забанен' if user['banned'] else '✅ активен'}\n\n"
        "Введите изменение баланса (10 = +10, -5 = −5) или используйте кнопки:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=ban_btn, callback_data=f"admuserban:{user['user_id']}")],
        ]),
    )


@router.callback_query(F.data.startswith("admuserban:"))
async def cb_user_ban(cb: CallbackQuery) -> None:
    await cb.answer()
    target = int(cb.data.split(":")[1])
    user = await db.get_user(target)
    if not user:
        await cb.answer("Не найден", show_alert=True)
        return
    await db.set_banned(target, not bool(user["banned"]))
    user = await db.get_user(target)
    await _edit_safe(
        cb.message,
        f"👤 <code>{target}</code> · статус: "
        f"{'🚫 забанен' if user['banned'] else '✅ активен'}",
        InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=("🚫 Забанить" if not user["banned"] else "✅ Разбанить"),
                callback_data=f"admuserban:{target}")],
        ]),
    )


@router.message(AdminStates.balance_amount)
async def st_balance_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().replace(",", ".")
    try:
        delta = float(raw)
    except ValueError:
        await message.answer("❌ Введите число (напр. 10 или -5):")
        return
    data = await state.get_data()
    target = data["target_user"]
    await db.change_balance(target, delta)
    user = await db.get_user(target)
    await state.clear()
    await message.answer(
        f"✅ Баланс изменён на {delta:+.2f}\n\n"
        f"👤 <code>{target}</code>\n"
        f"💰 Новый баланс: <code>{user['balance']:.2f} ₽</code>",
        reply_markup=admin_kb(),
    )
    with suppress(Exception):
        await message.bot.send_message(
            target,
            f"💠 Ваш баланс изменён администратором: {delta:+.2f} ₽\n"
            f"💰 Новый баланс: <code>{user['balance']:.2f} ₽</code>",
        )


@router.callback_query(F.data == "adm:deposits")
async def cb_deposits(cb: CallbackQuery) -> None:
    await cb.answer()
    conn = db._conn()
    cur = await conn.execute(
        """SELECT d.*, u.username, u.full_name FROM deposits d
           JOIN users u ON u.user_id = d.user_id
           ORDER BY d.id DESC LIMIT 10"""
    )
    rows = [dict(r) for r in await cur.fetchall()]
    dep_stats = await db.deposits_stats()
    lines = [
        "💳 <b>Пополнения</b>\n",
        f"✅ Зачислено всего: <code>{dep_stats['paid_sum']:.2f} ₽</code>",
        f"⏳ Ожидают оплаты: <b>{dep_stats['pending_cnt']}</b>\n",
    ]
    if not rows:
        lines.append("— записей нет —")
    for d in rows:
        emoji = {"paid": "✅", "pending": "⏳", "expired": "⌛️", "canceled": "❌"}.get(d["status"], "❔")
        name = d.get("username") or d.get("full_name") or d["user_id"]
        lines.append(
            f"{emoji} {name} · {d['amount']:.2f} · {provider_label(d['provider'])} · {d['created_at'][:16]}"
        )
    await _edit_safe(cb.message, "\n".join(lines), admin_kb())

"""Пользовательские сценарии: меню, каталог, покупка, профиль, история, пополнение."""
import logging
from contextlib import suppress
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardMarkup, InputMediaPhoto,
                           Message)

import config
import db
import keyboards as kb
import rates
import texts
from payments import create_invoice, get_invoice, available_providers, provider_label

log = logging.getLogger("user")

router = Router()


class UserStates(StatesGroup):
    deposit_amount = State()


# ---------------------------------------------------------------- helpers

async def _usd() -> float:
    return await rates.get_usd_rate()


async def _show_screen(message: Message, photo_id: Optional[str], text: str,
                       markup) -> None:
    """Показывает экран каталога: с фото — как фото с подписью, без — текстом.

    Фото→фото редактируется edit_media, текст→текст — edit_text.
    Переход текст↔фото отредактировать нельзя — удаляем и шлём заново.
    """
    is_media = getattr(message, "photo", None) is not None
    if photo_id and is_media:
        with suppress(Exception):
            await message.edit_media(
                InputMediaPhoto(media=photo_id, caption=text[:1024]),
                reply_markup=markup,
            )
            return
    if not photo_id and not is_media:
        with suppress(Exception):
            await message.edit_text(text, reply_markup=markup)
            return
    with suppress(Exception):
        await message.delete()
    if photo_id:
        await message.answer_photo(photo_id, caption=text[:1024], reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


async def show_main_menu(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        u = message.from_user
        await db.upsert_user(u.id, u.username or "", u.full_name)
        user = await db.get_user(u.id)
    if user["banned"]:
        await message.answer("⛔️ Вы заблокированы в этом магазине.")
        return
    usd = await _usd()
    await message.answer(
        texts.main_menu_text(user["full_name"], float(user["balance"]),
                             user["currency"], usd),
        reply_markup=kb.main_menu_kb(config.is_admin(message.from_user.id)),
    )


# ---------------------------------------------------------------- /start и меню

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_main_menu(message)


@router.message(F.text == "◀️ Назад")
@router.message(F.text == "🏠 Главное меню")
async def btn_back(message: Message, state: FSMContext) -> None:
    await state.clear()
    await show_main_menu(message)


@router.message(F.text == "🛍 Каталог")
async def btn_catalog(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup, photo_id = await kb.build_catalog(None)
    if markup is None:
        await message.answer("😔 Каталог пуст. Загляните позже!")
        return
    if photo_id:
        await message.answer_photo(photo_id, caption=text[:1024], reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


@router.message(F.text == "👤 Профиль")
async def btn_profile(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        await show_main_menu(message)
        return
    usd = await _usd()
    await message.answer(
        texts.profile_text(user, usd),
        reply_markup=kb.currency_kb(user["currency"]),
    )


@router.callback_query(F.data.startswith("cur:"))
async def cb_set_currency(cb: CallbackQuery) -> None:
    await cb.answer()
    code = cb.data.split(":")[1]
    if code not in rates.CURRENCY_NAMES:
        await cb.answer("Неизвестная валюта", show_alert=True)
        return
    user = await db.get_user(cb.from_user.id)
    if not user:
        return
    await db.set_currency(cb.from_user.id, code)
    user = await db.get_user(cb.from_user.id)
    usd = await _usd()
    with suppress(Exception):
        await cb.message.edit_text(
            texts.profile_text(user, usd),
            reply_markup=kb.currency_kb(code),
        )


@router.message(F.text == "📋 История")
async def btn_history(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    cur = user["currency"] if user else "RUB"
    usd = await _usd()
    purchases = await db.user_purchases(message.from_user.id, limit=10)
    if not purchases:
        await message.answer("📋 Пока нет покупок. Загляните в каталог «🛍»!")
        return
    lines = ["📋 <b>Последние покупки</b>\n"]
    for p in purchases:
        lines.append(
            f"• <b>{p['product_title']}</b> — <code>{rates.fmt(float(p['price']), cur, usd)}</code>\n"
            f"  📦 <code>{p['item_content']}</code>\n"
            f"  📅 {p['created_at']}\n"
        )
    await message.answer("\n".join(lines))


@router.message(F.text == "💬 Поддержка")
async def btn_support(message: Message) -> None:
    if config.SUPPORT_URL:
        from aiogram.types import InlineKeyboardButton
        await message.answer(
            "💬 <b>Поддержка</b>\n\n✉️ Если возникла проблема — напишите нам:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📨 Написать в поддержку", url=config.SUPPORT_URL)]
            ]),
        )
    else:
        await message.answer("💬 Поддержка временно недоступна.")


@router.message(F.text == "⚙️ Админка")
async def btn_admin(message: Message) -> None:
    from handlers import admin
    await admin.cmd_admin(message)


# ---------------------------------------------------------------- каталог: колбэки

@router.callback_query(F.data == "menu:catalog")
async def cb_catalog(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    text, markup, photo_id = await kb.build_catalog(None)
    if markup is None:
        await cb.answer("Каталог пуст", show_alert=True)
        return
    await _show_screen(cb.message, photo_id, text, markup)


@router.callback_query(F.data == "menu:main")
async def cb_main(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    user = await db.get_user(cb.from_user.id)
    balance = float(user["balance"]) if user else 0.0
    cur = user["currency"] if user else "RUB"
    usd = await _usd()
    # inline-кнопки не заменить reply-клавиатурой через edit — шлём новое сообщение
    await cb.message.answer(
        texts.main_menu_text(cb.from_user.full_name, balance, cur, usd),
        reply_markup=kb.main_menu_kb(config.is_admin(cb.from_user.id)),
    )


@router.callback_query(F.data == "noop")
async def cb_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    cat_id = int(cb.data.split(":")[1])
    cat = await db.get_category(cat_id)
    if not cat or not cat["is_visible"]:
        await cb.answer("Категория не найдена", show_alert=True)
        return
    text, markup, photo_id = await kb.build_catalog(cat_id)
    if markup is None:
        await cb.answer("Категория пуста", show_alert=True)
        return
    await _show_screen(cb.message, photo_id, text, markup)


@router.callback_query(F.data.startswith("prodp:"))
async def cb_products_page(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    parts = cb.data.split(":")
    cat_id = int(parts[1])
    page = int(parts[2])
    text, markup, photo_id = await kb.build_catalog(cat_id, prod_page=page)
    if markup is None:
        await cb.answer("Пусто", show_alert=True)
        return
    await _show_screen(cb.message, photo_id, text, markup)


# ---------------------------------------------------------------- товары

@router.callback_query(F.data.startswith("prod:"))
async def cb_product(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    product_id = int(cb.data.split(":")[1])
    product = await db.get_product(product_id)
    if not product or not product["is_visible"]:
        await cb.answer("Товар не найден", show_alert=True)
        return
    stock = await db.product_stock(product_id)
    user = await db.get_user(cb.from_user.id)
    cur = user["currency"] if user else "RUB"
    usd = await _usd()
    await _show_screen(
        cb.message,
        product["photo_id"],
        texts.product_text(product, stock, cur, usd),
        kb.product_kb(product_id, product["category_id"]),
    )


@router.callback_query(F.data.startswith("buy:"))
async def cb_buy(cb: CallbackQuery) -> None:
    await cb.answer()
    product_id = int(cb.data.split(":")[1])
    product = await db.get_product(product_id)
    if not product:
        await cb.answer("Товар не найден", show_alert=True)
        return
    stock = await db.product_stock(product_id)
    if stock < 1:
        await cb.answer("Нет в наличии", show_alert=True)
        return
    user = await db.get_user(cb.from_user.id)
    balance = float(user["balance"]) if user else 0.0
    cur = user["currency"] if user else "RUB"
    usd = await _usd()
    if balance < float(product["price"]):
        await cb.message.answer(
            texts.not_enough_money(float(product["price"]) - balance, cur, usd)
        )
        return
    await cb.message.answer(
        f"🛒 <b>Подтверждение покупки</b>\n\n"
        f"📦 Товар: <b>{product['title']}</b>\n"
        f"💵 Цена: <code>{rates.fmt_pair(float(product['price']), cur, usd)}</code>\n"
        f"💰 Ваш баланс: <code>{rates.fmt_pair(balance, cur, usd)}</code>\n\n"
        f"После покупки останется: <code>{rates.fmt_pair(balance - float(product['price']), cur, usd)}</code>",
        reply_markup=kb.confirm_buy_kb(product_id),
    )


@router.callback_query(F.data.startswith("buyok:"))
async def cb_buy_ok(cb: CallbackQuery) -> None:
    await cb.answer("Оформляем...")
    product_id = int(cb.data.split(":")[1])
    result = await db.buy_product(cb.from_user.id, product_id)
    user = await db.get_user(cb.from_user.id)
    cur = user["currency"] if user else "RUB"
    usd = await _usd()
    if not result.get("ok"):
        err = result.get("error")
        if err == "insufficient_funds":
            await cb.message.answer(texts.not_enough_money(result.get("need", 0), cur, usd))
        elif err == "out_of_stock":
            await cb.message.answer(texts.no_items())
        else:
            await cb.message.answer("❌ Не удалось оформить покупку. Попробуйте позже.")
        return
    await cb.message.edit_text(
        texts.purchase_success(result["content"], result["title"],
                               float(user["balance"]), cur, usd),
    )
    for admin_id in config.ADMIN_IDS:
        with suppress(Exception):
            await cb.bot.send_message(
                admin_id,
                f"🛒 <b>Новая покупка</b>\n\n"
                f"👤 {cb.from_user.full_name} (@{cb.from_user.username or '—'}) "
                f"<code>{cb.from_user.id}</code>\n"
                f"📦 {result['title']} — <code>{rates.fmt(float(result['price']), 'RUB', usd)}</code>",
            )


# ---------------------------------------------------------------- пополнение

@router.message(F.text == "💳 Пополнить")
async def btn_deposit(message: Message, state: FSMContext) -> None:
    await state.clear()
    if not available_providers():
        await message.answer(texts.deposit_no_providers())
        return
    usd = await _usd()
    await message.answer(
        texts.deposit_text() + f"\n\n💱 Текущий курс: <code>1 USDT = {usd:.2f} ₽</code>",
        reply_markup=kb.deposit_provider_kb(),
    )


@router.callback_query(F.data.startswith("dep:"))
async def cb_deposit_provider(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    provider = cb.data.split(":")[1]
    await state.set_state(UserStates.deposit_amount)
    await state.update_data(dep_provider=provider, dep_amount=None)
    await cb.message.answer(
        f"Выбрана система: <b>{provider_label(provider)}</b>\n\n"
        f"💵 Введите сумму пополнения в рублях "
        f"(минимум {config.MIN_DEPOSIT:.0f} ₽):",
        reply_markup=kb.deposit_amount_kb(),
    )


@router.callback_query(F.data.startswith("depamt:"))
async def cb_deposit_quick(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    amount_rub = float(cb.data.split(":")[1])
    data = await state.get_data()
    provider = data.get("dep_provider")
    if not provider:
        await cb.message.answer(
            "Сначала выберите платёжную систему:",
            reply_markup=kb.deposit_provider_kb(),
        )
        return
    await state.clear()
    await _create_and_show_invoice(cb.message, cb.from_user.id, provider, amount_rub)


async def _create_and_show_invoice(message: Message, user_id: int, provider: str,
                                    amount_rub: float) -> None:
    if amount_rub < config.MIN_DEPOSIT:
        await message.answer(
            f"❌ Минимальная сумма: <code>{config.MIN_DEPOSIT:.0f} ₽</code>"
        )
        return
    payload = f"dep_{user_id}_{db.random_code(8)}"
    inv = await create_invoice(provider, amount_rub, payload)
    if not inv:
        await message.answer(
            "❌ Не удалось создать счёт. Попробуйте позже или другую платёжную систему:",
            reply_markup=kb.deposit_provider_kb(),
        )
        return
    await db.create_deposit(user_id, provider, inv["invoice_id"], amount_rub, amount_rub)
    usd_rate = inv.get("usd_rate", await _usd())
    await message.answer(
        f"🧾 <b>Счёт создан</b> ({provider_label(provider)})\n\n"
        f"➕ Зачислится на баланс: <code>{amount_rub:.0f} ₽</code>\n"
        f"💵 К оплате: <code>{inv['amount_usdt']:.2f} USDT</code>\n"
        f"💱 По курсу: 1 $ = {usd_rate:.2f} ₽\n\n"
        f"Оплатите по кнопке ниже — зачисление произойдёт <b>автоматически</b> "
        f"(проверка каждые ~20 секунд).",
        reply_markup=kb.invoice_kb(inv["pay_url"], inv["invoice_id"]),
    )


@router.message(UserStates.deposit_amount)
async def st_deposit_amount(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    provider = data.get("dep_provider")
    if not provider:
        await state.clear()
        await message.answer(
            "Сначала выберите платёжную систему:",
            reply_markup=kb.deposit_provider_kb(),
        )
        return
    amount_rub = db.parse_amount(message.text)
    if not amount_rub:
        await message.answer("❌ Введите корректное число, например: 500")
        return
    await state.clear()
    await _create_and_show_invoice(message, message.from_user.id, provider, amount_rub)


@router.callback_query(F.data.startswith("depchk:"))
async def cb_deposit_check(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer("Проверяем оплату...")
    invoice_id = cb.data.split(":")[1]
    deposit = await db.get_deposit_by_invoice(invoice_id)
    if not deposit:
        await cb.message.answer("❌ Счёт не найден.")
        return
    inv = await get_invoice(deposit["provider"], invoice_id)
    if inv and inv.get("status") == "paid":
        credited = await db.mark_deposit_paid(deposit["id"])
        user = await db.get_user(deposit["user_id"])
        cur = user["currency"] if user else "RUB"
        usd = await _usd()
        if credited:
            await cb.message.answer(
                f"✅ Оплата получена! Баланс пополнен на "
                f"<code>{rates.fmt(float(deposit['credit']), 'RUB', usd)}</code>\n"
                f"💰 Текущий баланс: <code>{rates.fmt_pair(float(user['balance']), cur, usd)}</code>"
            )
            for admin_id in config.ADMIN_IDS:
                with suppress(Exception):
                    await cb.bot.send_message(
                        admin_id,
                        f"💰 <b>Пополнение</b> ({provider_label(deposit['provider'])})\n"
                        f"👤 {cb.from_user.full_name} <code>{deposit['user_id']}</code>\n"
                        f"💵 {inv.get('amount_usdt', deposit['amount']):.2f} USDT → "
                        f"баланс +{deposit['credit']:.0f} ₽",
                    )
        else:
            await cb.message.answer(
                f"ℹ️ Этот счёт уже зачислен.\n"
                f"💰 Баланс: <code>{rates.fmt_pair(float(user['balance']), cur, usd)}</code>"
            )
    elif inv and inv.get("status") == "expired":
        await db.mark_deposit_status(deposit["id"], "expired")
        await cb.message.answer("⌛️ Счёт истёк. Создайте новый в разделе «💰 Пополнить».")
    else:
        await cb.message.answer(
            "⏳ Оплата пока не поступила. Баланс зачислится автоматически — "
            "можете проверить позже."
        )


# ---------------------------------------------------------------- fallback

@router.message()
async def fallback(message: Message) -> None:
    await message.answer("🤔 Не понимаю. Воспользуйтесь кнопками меню.")

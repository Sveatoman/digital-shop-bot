"""Тексты сообщений бота. Все суммы хранятся в ₽; отображение через rates.fmt."""
import config
import rates


def main_menu_text(full_name: str, balance_rub: float, currency: str, usd_rate: float) -> str:
    return (
        f"✨ {config.SHOP_NAME} ✨\n\n"
        f"👋 Привет, <b>{full_name}</b>!\n\n"
        f"💰 Баланс: <code>{rates.fmt(balance_rub, currency, usd_rate)}</code>\n\n"
        f"🎯 Выберите действие:"
    )


def profile_text(user: dict, usd_rate: float) -> str:
    cur = user.get("currency", "RUB")
    return (
        "👤 <b>Ваш профиль</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"📝 Имя: <b>{user['full_name']}</b>\n"
        f"🔗 Username: @{user['username'] or '—'}\n\n"
        f"💰 Баланс: <code>{rates.fmt_pair(float(user['balance']), cur, usd_rate)}</code>\n"
        f"🛍 Всего покупок: <b>{user['purchases_cnt']}</b>\n"
        f"💎 Потрачено: <code>{rates.fmt_pair(float(user['total_spent']), cur, usd_rate)}</code>\n\n"
        f"💱 Валюта отображения: <b>{rates.CURRENCY_NAMES.get(cur, cur)}</b>"
    )


def deposit_text() -> str:
    return (
        "💎 <b>Пополнение баланса</b>\n\n"
        f"🔹 Выберите платёжную систему, затем укажите сумму.\n"
        f"📊 Минимум: <code>{config.MIN_DEPOSIT:.0f} ₽</code>\n\n"
        "💱 Счёт выставляется в USDT по рыночному курсу,\n"
        "✅ На баланс зачисляется ровно та сумма в рублях, что вы указали."
    )


def deposit_no_providers() -> str:
    return (
        "❌ Платёжные провайдеры не настроены.\n\n"
        "Администратору: заполните CRYPTOBOT_TOKEN и/или XROCKET_TOKEN в .env"
    )


def product_text(product: dict, stock: int, currency: str, usd_rate: float) -> str:
    return (
        f"📦 <b>{product['title']}</b>\n\n"
        f"{product['description'] or '—'}\n\n"
        f"💵 Цена: <code>{rates.fmt_pair(float(product['price']), currency, usd_rate)}</code>\n"
        f"📦 В наличии: <b>{stock} шт</b>"
    )


def not_enough_money(need_rub: float, currency: str, usd_rate: float) -> str:
    return (
        f"❌ Недостаточно средств.\n\n"
        f"💸 Не хватает: <code>{rates.fmt_pair(need_rub, currency, usd_rate)}</code>\n\n"
        "💳 Пополните баланс в разделе «Пополнить»."
    )


def purchase_success(content: str, title: str, balance_rub: float,
                     currency: str, usd_rate: float) -> str:
    return (
        "🎉 <b>Покупка успешна!</b>\n\n"
        f"📦 Товар: <b>{title}</b>\n\n"
        f"━━━━━━ ВАШ ТОВАР ━━━━━━\n"
        f"<code>{content}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Остаток баланса: <code>{rates.fmt_pair(balance_rub, currency, usd_rate)}</code>\n\n"
        f"💾 Сохраните данные — они всегда доступны в разделе «📜 История»."
    )


def no_items() -> str:
    return "❌ Этого товара сейчас нет в наличии. Обратитесь к администратору."

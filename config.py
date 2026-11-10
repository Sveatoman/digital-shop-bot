"""Конфигурация магазина. Все настройки читаются из .env / переменных окружения.

Деньги: баланс и цены хранятся в РУБЛЯХ. Счета выставляются в USDT
по рыночному курсу USDT (Binance, см. rates.py). В профиле пользователь выбирает валюту отображения.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _get_admin_ids() -> list[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids = []
    for part in raw.replace(" ", "").split(","):
        if part.strip().isdigit():
            ids.append(int(part))
    return ids


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_IDS: list[int] = _get_admin_ids()

DB_PATH: str = os.getenv("DB_PATH", "shop.db")

CRYPTOBOT_TOKEN: str = os.getenv("CRYPTOBOT_TOKEN", "")
XROCKET_TOKEN: str = os.getenv("XROCKET_TOKEN", "")

# Курс: auto — Binance USDTRUB (фолбэк open.er-api.com), binance — только Binance,
# manual — всегда USD_RATE
RATE_MODE: str = os.getenv("RATE_MODE", "auto").lower()
USD_RATE: float = float(os.getenv("USD_RATE", "92.0"))  # ₽ за 1 $, резерв

# Минимальная сумма пополнения в рублях
MIN_DEPOSIT: float = float(os.getenv("MIN_DEPOSIT", "100"))

SHOP_NAME: str = os.getenv("SHOP_NAME", "💎 Digital Shop")
SUPPORT_URL: str = os.getenv("SUPPORT_URL", "")

PAGINATION_LIMIT: int = 6  # категорий/товаров на странице


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

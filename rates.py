"""Курсы валют и форматирование денег.

Баланс и цены магазина хранятся в рублях (₽). Счета выставляются в USDT.

Источник курса ₽ за 1 USDT (по порядку приоритета, с кэшем на 30 минут):
1. CryptoBot getExchangeRates — курс самой платёжки, по которому она
   оценивает USDT (бесплатно, без токена)
2. Binance ticker USDTRUB (last price) — рыночная цена
   (спотовая пара неликвидна, курс может быть завышен — потому №2)
3. open.er-api.com (USD→RUB, обновление раз в сутки) — примерный фолбэк
4. USD_RATE из .env — резерв при полной недоступности сети

RATE_MODE=manual — всегда USD_RATE из .env.
Кэш общий, поэтому при смене источника расхождений в боте не будет.
"""
import asyncio
import logging
import time
from typing import Optional

import aiohttp

import config
import httpclient

log = logging.getLogger("rates")

CRYPTOBOT_RATES_URL = "https://pay.crypt.bot/api/getExchangeRates"
BINANCE_URL = "https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB"
ER_API_URL = "https://open.er-api.com/v6/latest/USD"
CACHE_TTL = 1800  # секунд (30 минут)

_cached_rate: Optional[float] = None
_cached_ts: float = 0.0
_cached_src: str = ""

SYMBOLS = {"RUB": "₽", "USD": "$"}
CURRENCY_NAMES = {"RUB": "Рубли (₽)", "USD": "Доллары ($)"}


async def _fetch_json(url: str, headers: dict | None = None) -> Optional[dict]:
    session = httpclient.get_session()
    try:
        async with session.get(url, headers=headers or {}) as resp:
            if resp.status != 200:
                log.warning("%s ответил HTTP %s", url, resp.status)
                return None
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("%s недоступен: %s", url, type(e).__name__)
        return None


async def _rate_from_cryptobot() -> Optional[float]:
    """Курс USDT→RUB из самого CryptoBot (требует API-токен приложения)."""
    if not config.CRYPTOBOT_TOKEN:
        return None
    try:
        data = await _fetch_json(
            CRYPTOBOT_RATES_URL,
            headers={"Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN},
        )
    except asyncio.TimeoutError:
        return None
    if not data or not data.get("ok"):
        return None
    for row in data.get("result") or []:
        if row.get("source") == "USDT" and row.get("target") == "RUB":
            try:
                rate = float(row["rate"])
                if rate > 0:
                    return rate
            except (KeyError, ValueError, TypeError):
                continue
    return None


async def _rate_from_binance() -> Optional[float]:
    data = await _fetch_json(BINANCE_URL)
    if not data:
        return None
    try:
        rate = float(data["price"])
        return rate if rate > 0 else None
    except (KeyError, ValueError, TypeError):
        return None


async def _rate_from_erapi() -> Optional[float]:
    data = await _fetch_json(ER_API_URL)
    if not data or data.get("result") != "success":
        return None
    try:
        rate = float(data["rates"]["RUB"])
        return rate if rate > 0 else None
    except (KeyError, ValueError, TypeError):
        return None


async def get_usd_rate() -> float:
    """₽ за 1 USDT: CryptoBot → Binance → er-api → устаревший кэш → USD_RATE."""
    global _cached_rate, _cached_ts, _cached_src
    if config.RATE_MODE == "manual":
        return config.USD_RATE
    now = time.monotonic()
    if _cached_rate is not None and now - _cached_ts < CACHE_TTL:
        return _cached_rate

    rate: Optional[float] = None
    src = ""
    for name, fetcher in (
        ("cryptobot", _rate_from_cryptobot),
        ("binance", _rate_from_binance),
        ("er-api", _rate_from_erapi),
    ):
        try:
            rate = await fetcher()
        except Exception as e:
            log.warning("Источник %s упал: %s", name, e)
            rate = None
        if rate is not None and rate > 0:
            src = name
            break

    if rate is not None and rate > 0:
        _cached_rate = rate
        _cached_ts = now
        _cached_src = src
        return rate

    log.warning("Курс не получен (провалились все источники)")
    if _cached_rate is not None:
        return _cached_rate  # устаревший кэш лучше константы
    return config.USD_RATE


def rate_source() -> str:
    """Имя источника последнего успешного запроса (для диагностики)."""
    return _cached_src or "manual"


def fmt(amount_rub: float, currency: str, usd_rate: float) -> str:
    """Форматирует сумму (хранится в ₽) в выбранной валюте отображения."""
    if currency == "USD" and usd_rate > 0:
        return f"{amount_rub / usd_rate:.2f} $"
    return f"{amount_rub:.2f} ₽"


def fmt_pair(amount_rub: float, currency: str, usd_rate: float) -> str:
    """Основная валюта + эквивалент: «100.00 ₽ (≈1.08 $)»."""
    if currency == "USD" and usd_rate > 0:
        return f"{amount_rub / usd_rate:.2f} $ (≈{amount_rub:.2f} ₽)"
    if usd_rate > 0:
        return f"{amount_rub:.2f} ₽ (≈{amount_rub / usd_rate:.2f} $)"
    return f"{amount_rub:.2f} ₽"

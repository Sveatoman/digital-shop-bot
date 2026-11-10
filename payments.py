"""Платёжные провайдеры: CryptoBot (Crypto Pay API) и XRocket Crypto.

Все суммы внутри магазина — в РУБЛЯХ. Счета у провайдеров выставляются в USDT:
при создании инвойса сумма конвертируется по курсу (rates.get_usd_rate).

Интерфейс единый:
    await create_invoice(provider, amount_rub, payload) -> dict | None
        dict: invoice_id, pay_url, status, amount_usdt (сумма счёта в USDT)
    await get_invoice(provider, invoice_id) -> dict | None

CryptoBot Crypto Pay API:
    POST https://pay.crypt.bot/api/createInvoice
    GET  https://pay.crypt.bot/api/getInvoices?invoice_ids=...
    Заголовок: Crypto-Pay-API-Token
    Статусы: active / paid / expired

XRocket Crypto API:
    POST https://api.xrocket.pro/crypto/v1/invoices
    GET  https://api.xrocket.pro/crypto/v1/invoices/{id}
    Заголовок: API-KEY
    Статусы: active / paid / expired
"""
import asyncio
import logging
from typing import Any, Optional

import aiohttp

import config
import httpclient
import rates

log = logging.getLogger("payments")

CRYPTOBOT_BASE = "https://pay.crypt.bot/api"
XROCKET_BASE = "https://api.xrocket.pro/crypto/v1"


def available_providers() -> list[str]:
    result = []
    if config.CRYPTOBOT_TOKEN:
        result.append("cryptobot")
    if config.XROCKET_TOKEN:
        result.append("xrocket")
    return result


def provider_label(provider: str) -> str:
    return {"cryptobot": "CryptoBot", "xrocket": "XRocket"}.get(provider, provider)


async def _post(url: str, headers: dict, json_data: dict) -> Optional[dict]:
    try:
        session = httpclient.get_session()
        async with session.post(url, headers=headers, json=json_data) as resp:
            data = await resp.json(content_type=None)
            return data if resp.status < 500 else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("POST %s failed: %s", url, e)
        return None


async def _get(url: str, headers: dict) -> Optional[dict]:
    try:
        session = httpclient.get_session()
        async with session.get(url, headers=headers) as resp:
            data = await resp.json(content_type=None)
            return data if resp.status < 500 else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        log.warning("GET %s failed: %s", url, e)
        return None


# ------------------------------------------------------------------ CryptoBot

async def _cryptobot_create(amount_usdt: float, payload: str) -> Optional[dict]:
    data = await _post(
        f"{CRYPTOBOT_BASE}/createInvoice",
        headers={"Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN},
        json_data={
            "asset": "USDT",
            "amount": f"{amount_usdt:.2f}",
            "payload": payload,
            "description": f"Пополнение {config.SHOP_NAME}",
        },
    )
    if not data or not data.get("ok"):
        log.warning("CryptoBot createInvoice error: %s", data)
        return None
    inv = data.get("result") or {}
    if not inv:
        return None
    return {
        "invoice_id": str(inv.get("invoice_id", "")),
        "pay_url": inv.get("pay_url") or inv.get("bot_invoice_url", ""),
        "status": "pending",
        "amount_usdt": float(inv.get("amount", amount_usdt)),
    }


async def _cryptobot_get(invoice_id: str) -> Optional[dict]:
    data = await _get(
        f"{CRYPTOBOT_BASE}/getInvoices?invoice_ids={invoice_id}",
        headers={"Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN},
    )
    if not data or not data.get("ok"):
        return None
    items = (data.get("result") or {}).get("items") or []
    if not items:
        return None
    inv = items[0]
    status = "paid" if inv.get("status") == "paid" else (
        "expired" if inv.get("status") == "expired" else "pending"
    )
    return {
        "invoice_id": str(inv.get("invoice_id", invoice_id)),
        "pay_url": inv.get("pay_url") or "",
        "status": status,
        "amount_usdt": float(inv.get("amount", 0) or 0),
    }


# ------------------------------------------------------------------ XRocket

async def _xrocket_create(amount_usdt: float, payload: str) -> Optional[dict]:
    data = await _post(
        f"{XROCKET_BASE}/invoices",
        headers={"API-KEY": config.XROCKET_TOKEN},
        json_data={
            "currency": "USDT",
            "amount": f"{amount_usdt:.2f}",
            "description": f"Пополнение {config.SHOP_NAME}",
            "payload": payload,
            "allowAnonymous": False,
            "expiresInSeconds": 3600,
        },
    )
    if not data:
        return None
    # XRocket: {"success": true, "data": {...}} или старый формат {"id": ...}
    inv = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(inv, dict):
        return None
    if not (inv.get("id") or inv.get("invoiceId")):
        log.warning("XRocket createInvoice error: %s", data)
        return None
    invoice_id = str(inv.get("id") or inv.get("invoiceId"))
    return {
        "invoice_id": invoice_id,
        "pay_url": inv.get("link") or inv.get("payUrl") or f"https://t.me/xRocketCryptoBot?start={invoice_id}",
        "status": "pending",
        "amount_usdt": amount_usdt,
    }


async def _xrocket_get(invoice_id: str) -> Optional[dict]:
    data = await _get(
        f"{XROCKET_BASE}/invoices/{invoice_id}",
        headers={"API-KEY": config.XROCKET_TOKEN},
    )
    if not data:
        return None
    inv = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(inv, dict):
        return None
    status = str(inv.get("status", "")).lower()
    if status in ("paid", "completed", "confirmed"):
        norm = "paid"
    elif status in ("expired", "cancelled", "canceled"):
        norm = "expired"
    else:
        norm = "pending"
    return {
        "invoice_id": str(inv.get("id") or invoice_id),
        "pay_url": inv.get("link") or "",
        "status": norm,
        "amount_usdt": float(inv.get("amount", 0) or 0),
    }


# ------------------------------------------------------------------ единый интерфейс

async def create_invoice(provider: str, amount_rub: float, payload: str) -> Optional[dict]:
    """Создаёт счёт на сумму в рублях, конвертированную в USDT по курсу.

    Возвращает нормализованный dict (или None при ошибке).
    """
    usd_rate = await rates.get_usd_rate()
    if usd_rate <= 0:
        log.error("Некорректный курс USD: %s", usd_rate)
        return None
    amount_usdt = round(amount_rub / usd_rate, 2)
    if amount_usdt < 0.01:
        log.warning("Сумма счёта в USDT слишком мала: %s (₽%s)", amount_usdt, amount_rub)
        return None

    if provider == "cryptobot" and config.CRYPTOBOT_TOKEN:
        result = await _cryptobot_create(amount_usdt, payload)
    elif provider == "xrocket" and config.XROCKET_TOKEN:
        result = await _xrocket_create(amount_usdt, payload)
    else:
        return None
    if result is not None:
        result["amount_rub"] = amount_rub
        result["usd_rate"] = usd_rate
    return result


async def get_invoice(provider: str, invoice_id: str) -> Optional[dict]:
    """Возвращает нормализованный статус инвойса (или None, если не удалось получить)."""
    if provider == "cryptobot" and config.CRYPTOBOT_TOKEN:
        return await _cryptobot_get(invoice_id)
    if provider == "xrocket" and config.XROCKET_TOKEN:
        return await _xrocket_get(invoice_id)
    return None


async def get_invoices_bulk(provider: str, invoice_ids: list[str]) -> Optional[dict[str, dict]]:
    """Пакетная проверка инвойсов: {invoice_id: norm_invoice} одним запросом.

    Пока поддерживает только CryptoBot (getInvoices принимает список id).
    Для остальных провайдеров возвращает None — вызывающий код проверит по одному.
    """
    if provider != "cryptobot" or not config.CRYPTOBOT_TOKEN or not invoice_ids:
        return None
    ids_param = ",".join(str(i) for i in invoice_ids)
    data = await _get(
        f"{CRYPTOBOT_BASE}/getInvoices?invoice_ids={ids_param}",
        headers={"Crypto-Pay-API-Token": config.CRYPTOBOT_TOKEN},
    )
    if not data or not data.get("ok"):
        return None
    result: dict[str, dict] = {}
    for inv in (data.get("result") or {}).get("items") or []:
        status = "paid" if inv.get("status") == "paid" else (
            "expired" if inv.get("status") == "expired" else "pending"
        )
        result[str(inv.get("invoice_id"))] = {
            "invoice_id": str(inv.get("invoice_id", "")),
            "pay_url": inv.get("pay_url") or "",
            "status": status,
            "amount_usdt": float(inv.get("amount", 0) or 0),
        }
    return result


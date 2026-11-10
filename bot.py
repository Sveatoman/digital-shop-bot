"""Точка входа: запуск бота + фоновая задача проверки инвойсов."""
import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import config
import db
import httpclient
import rates
from handlers import user, admin
from payments import get_invoices_bulk, provider_label

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("bot")

CHECK_INTERVAL = 20       # секунд между проверками инвойсов
BACKOFF_MAX = 300         # максимум при сетевых сбоях


async def get_invoice_safe(provider: str, invoice_id: str):
    """get_invoice, но сетевые ошибки возвращают None и увеличивают интервал поллера."""
    from payments import get_invoice
    try:
        return await get_invoice(provider, invoice_id)
    except Exception as e:
        log.warning("get_invoice(%s) упал: %s", invoice_id, e)
        return None


async def _credit_deposit(bot: Bot, dep: dict) -> None:
    """Зачисляет оплаченный депозит и рассылает уведомления (одна логика для poller и кнопки)."""
    credited = await db.mark_deposit_paid(dep["id"])
    if not credited:
        return
    user_row = await db.get_user(dep["user_id"])
    usd = await rates.get_usd_rate()
    cur = user_row["currency"] if user_row else "RUB"
    try:
        await bot.send_message(
            dep["user_id"],
            f"✅ Оплата получена! Баланс пополнен на "
            f"<code>{dep['credit']:.0f} ₽</code>.\n"
            f"💰 Текущий баланс: <code>{rates.fmt(float(user_row['balance']), cur, usd)}</code>",
        )
    except Exception as e:
        log.info("Не удалось уведомить %s: %s", dep["user_id"], e)
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 <b>Автозачисление</b> ({provider_label(dep['provider'])})\n"
                f"👤 <code>{dep['user_id']}</code>\n"
                f"💵 +{dep['credit']:.0f} ₽",
            )
        except Exception:
            pass


async def invoices_poller(bot: Bot) -> None:
    """Фоновая проверка pending-инвойсов.

    CryptoBot проверяет пачкой (getInvoices принимает список id — один запрос
    на все инвойсы), XRocket — по одному. При сетевых сбоях интервал
    растёт экспоненциально до BACKOFF_MAX.
    """
    log.info("Invoice poller запущен (интервал %ss)", CHECK_INTERVAL)
    interval = CHECK_INTERVAL
    while True:
        try:
            pending = await db.pending_deposits()
            if pending:
                cb_ids = [d for d in pending if d["provider"] == "cryptobot"]
                xr_ids = [d for d in pending if d["provider"] == "xrocket"]

                if cb_ids:
                    bulk = await get_invoices_bulk(
                        "cryptobot", [d["invoice_id"] for d in cb_ids]
                    )
                    if bulk is not None:  # None = сетевая ошибка, пропускаем
                        for dep in cb_ids:
                            inv = bulk.get(dep["invoice_id"])
                            if not inv:
                                continue
                            if inv.get("status") == "paid":
                                await _credit_deposit(bot, dep)
                            elif inv.get("status") == "expired":
                                await db.mark_deposit_status(dep["id"], "expired")

                for dep in xr_ids:
                    inv = await get_invoice_safe("xrocket", dep["invoice_id"])
                    if inv and inv.get("status") == "paid":
                        await _credit_deposit(bot, dep)
                    elif inv and inv.get("status") == "expired":
                        await db.mark_deposit_status(dep["id"], "expired")
            if interval != CHECK_INTERVAL:
                interval = CHECK_INTERVAL
                log.info("Сеть восстановилась: интервал %ss", interval)
        except Exception as e:
            interval = min(interval * 2, BACKOFF_MAX)
            log.exception("Ошибка в poller: %s (интервал → %ss)", e, interval)
        await asyncio.sleep(interval)


async def main() -> None:
    if not config.BOT_TOKEN:
        print("❌ BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните.")
        sys.exit(1)

    await db.init_db()

    # прогрев кэша курса до старта поллинга (плюс лог источника)
    usd = await rates.get_usd_rate()
    log.info("Курс USDT: %.2f ₽ (источник: %s)", usd, rates.rate_source())

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # роутеры: админские first (фильтр по callback-префиксам не даёт пересечений,
    # но пусть admin обрабатывает свои команды раньше)
    dp.include_router(admin.router)
    dp.include_router(user.router)

    poller = asyncio.create_task(invoices_poller(bot))
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        log.info("Бот запущен")
        await dp.start_polling(bot)
    finally:
        poller.cancel()
        await db.close_db()
        await httpclient.close_session()
        await bot.session.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Бот остановлен")

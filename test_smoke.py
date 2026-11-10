"""Smoke-тест: инициализация БД и полный сценарий без Telegram (запускать можно без токена).

python test_smoke.py
"""
import asyncio
import os
import sys

os.environ["DB_PATH"] = "test_shop.db"
os.environ["RATE_MODE"] = "manual"
os.environ["USD_RATE"] = "90.0"

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import db  # noqa: E402
import rates  # noqa: E402


async def main() -> None:
    await db.init_db()
    ok = lambda label: print(f"  ✅ {label}")

    # --- пользователь и валюта
    await db.upsert_user(1, "tester", "Тест Тестов")
    u = await db.get_user(1)
    assert u and u["balance"] == 0 and u["currency"] == "RUB"
    ok("создание пользователя, валюта по умолчанию RUB")

    await db.upsert_user(1, "tester2", "Тест Тестов")
    u = await db.get_user(1)
    assert u["username"] == "tester2" and u["balance"] == 0 and u["currency"] == "RUB"
    ok("upsert не сбрасывает баланс и валюту")

    await db.set_currency(1, "USD")
    assert await db.get_currency(1) == "USD"
    await db.set_currency(1, "RUB")
    ok("смена валюты отображения")

    # --- категории
    root = await db.create_category(None, "Аккаунты")
    sub = await db.create_category(root, "Steam")
    assert (await db.get_category(root))["title"] == "Аккаунты"
    assert (await db.child_categories(root))[0]["id"] == sub
    ok("категории и подкатегории")

    # --- товары и склад (цены в рублях)
    prod = await db.create_product(sub, "Steam Basic", "Описание", 1000.0)
    await db.add_product_items(prod, ["acc1:pass1", "acc2:pass2", "acc3:pass3"])
    assert await db.product_stock(prod) == 3
    ok("товар + 3 позиции")

    # --- покупка без денег
    res = await db.buy_product(1, prod)
    assert res == {"ok": False, "error": "insufficient_funds", "need": 1000.0}
    ok("покупка без денег отклонена")

    # --- покупка с деньгами
    await db.change_balance(1, 2500.0)
    res = await db.buy_product(1, prod)
    assert res["ok"] and res["content"] == "acc1:pass1"
    assert await db.product_stock(prod) == 2
    u = await db.get_user(1)
    assert u["balance"] == 1500.0 and u["purchases_cnt"] == 1 and u["total_spent"] == 1000.0
    purchases = await db.user_purchases(1)
    assert purchases[0]["item_content"] == "acc1:pass1"
    ok("автовыдача: списание, выдача, история")

    # --- склад закончился
    await db.change_balance(1, 10000.0)
    for _ in range(2):
        await db.buy_product(1, prod)
    assert await db.product_stock(prod) == 0
    res = await db.buy_product(1, prod)
    assert res["ok"] is False and res["error"] == "out_of_stock"
    ok("склад закончился → out_of_stock")

    # --- фото категорий и товаров
    await db.update_category(sub, photo_id="AgACAgIAAx0_cat")
    assert (await db.get_category(sub))["photo_id"] == "AgACAgIAAx0_cat"
    await db.update_category(sub, photo_id=None)
    assert (await db.get_category(sub))["photo_id"] is None
    await db.update_product(prod, photo_id="AgACAgIAAx0_prod")
    assert (await db.get_product(prod))["photo_id"] == "AgACAgIAAx0_prod"
    await db.update_product(prod, photo_id=None)
    assert (await db.get_product(prod))["photo_id"] is None
    ok("фото категорий и товаров: установка и сброс")

    await db.update_category(root, photo_id="root_photo")
    await db.update_product(prod, photo_id="prod_photo")
    import keyboards as kb_mod
    text, markup, cat_photo = await kb_mod.build_catalog(sub)
    assert cat_photo is None and markup is not None
    text, markup, cat_photo = await kb_mod.build_catalog(root)
    assert cat_photo == "root_photo"
    ok("каталог возвращает фото категории")
    # баланс: 0 +2500 -1000 +10000 -1000 -1000 = 9500
    u = await db.get_user(1)
    assert u["balance"] == 9500.0
    dep = await db.create_deposit(1, "cryptobot", "inv-1", 1000.0, 1000.0)
    assert await db.mark_deposit_paid(dep) is True
    assert await db.mark_deposit_paid(dep) is False  # повторно не зачислит
    u = await db.get_user(1)
    assert u["balance"] == 10500.0
    ok("депозит: зачисление ровно один раз")

    # --- курсы и форматирование (manual: 90 ₽/$)
    usd = await rates.get_usd_rate()
    assert usd == 90.0
    assert rates.fmt(900.0, "RUB", usd) == "900.00 ₽"
    assert rates.fmt(900.0, "USD", usd) == "10.00 $"
    assert "900.00 ₽" in rates.fmt_pair(900.0, "RUB", usd)
    assert "10.00 $" in rates.fmt_pair(900.0, "USD", usd)
    ok("конвертация и форматирование валют")

    # --- миграция: старая база без колонки currency
    await db.close_db()
    import aiosqlite
    async with aiosqlite.connect("test_shop.db") as conn:
        await conn.execute("CREATE TABLE users_old AS SELECT user_id, username, full_name, balance, "
                           "total_spent, purchases_cnt, banned, created_at, updated_at FROM users")
        await conn.execute("DROP TABLE users")
        await conn.execute("ALTER TABLE users_old RENAME TO users")
        await conn.commit()
    await db.init_db()
    u = await db.get_user(1)
    assert u["currency"] == "RUB" and u["balance"] == 10500.0
    ok("миграция старой базы: currency добавлен, данные целы")

    # --- рассылка-состояние
    await db.save_broadcast_state("hello", None, None, 10)
    st = await db.get_broadcast_state()
    assert st["text"] == "hello" and st["total"] == 10
    await db.update_broadcast_progress(5, 1)
    await db.clear_broadcast_state()
    assert await db.get_broadcast_state() is None
    ok("состояние рассылки")

    # --- статистика
    assert await db.count_users() == 1
    assert abs(await db.total_revenue() - 3000.0) < 1e-9
    ok("статистика")

    await db.close_db()
    print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ")


if __name__ == "__main__":
    asyncio.run(main())

"""Слой доступа к данным (SQLite через aiosqlite).

Таблицы:
- users            — покупатели: баланс, статистика покупок
- categories       — дерево категорий (parent_id = NULL для корневых; photo_id — фото в каталоге)
- products         — товары, привязаны к категории (photo_id — фото в каталоге)
- product_items    — «единицы» товара: данные для автовыдачи (по одной на покупку)
- purchases        — история покупок (что и кому выдано)
- deposits         — платежи/инвойсы CryptoBot и XRocket
- settings         — key/value настройки (напр. уведомления админам о покупках)
- broadcast_state  — состояние рассылки (для прогресса и возобновления)
"""
import datetime as dt
import json
import random
import string
from typing import Any, Optional

import aiosqlite

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    full_name     TEXT,
    balance       REAL NOT NULL DEFAULT 0,
    total_spent   REAL NOT NULL DEFAULT 0,
    purchases_cnt INTEGER NOT NULL DEFAULT 0,
    banned        INTEGER NOT NULL DEFAULT 0,
    currency      TEXT NOT NULL DEFAULT 'RUB',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
    title     TEXT NOT NULL,
    is_visible INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    photo_id  TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price       REAL NOT NULL,
    is_visible  INTEGER NOT NULL DEFAULT 1,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    photo_id    TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    content    TEXT NOT NULL,
    sold_at    TEXT
);

CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    product_id INTEGER,
    product_title TEXT NOT NULL,
    price      REAL NOT NULL,
    item_content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deposits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(user_id),
    provider   TEXT NOT NULL,             -- cryptobot | xrocket
    invoice_id TEXT NOT NULL,             -- id инвойса у провайдера
    amount     REAL NOT NULL,             -- сумма в CURRENCY
    credit     REAL NOT NULL,             -- сколько зачислится на баланс
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending | paid | expired | canceled
    created_at TEXT NOT NULL,
    paid_at    TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS broadcast_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    text TEXT,
    photo TEXT,
    keyboard_json TEXT,
    total INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_items_product ON product_items(product_id);
CREATE INDEX IF NOT EXISTS idx_items_sold ON product_items(sold_at);
CREATE INDEX IF NOT EXISTS idx_categories_parent ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_purchases_user ON purchases(user_id);
CREATE INDEX IF NOT EXISTS idx_deposits_invoice ON deposits(invoice_id);
"""

_db: Optional[aiosqlite.Connection] = None


async def init_db() -> None:
    global _db
    _db = await aiosqlite.connect(config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    # Оптимизация SQLite для производительности
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA synchronous=NORMAL")
    await _db.execute("PRAGMA cache_size=-64000")  # 64MB cache
    await _db.execute("PRAGMA temp_store=MEMORY")
    await _db.executescript(SCHEMA)
    # миграции для существующих баз
    await _migrate()
    await _db.commit()


async def _migrate() -> None:
    async def _add_column(table: str, column: str, ddl_type: str) -> None:
        cur = await _db.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cur.fetchall()}
        if column not in cols:
            await _db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

    await _add_column("users", "currency", "TEXT NOT NULL DEFAULT 'RUB'")
    await _add_column("categories", "photo_id", "TEXT")
    await _add_column("products", "photo_id", "TEXT")
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db:
        await _db.close()
        _db = None


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> aiosqlite.Connection:
    assert _db is not None, "База не инициализирована: сначала init_db()"
    return _db


# ---------------------------------------------------------------- users

async def get_user(user_id: int) -> Optional[dict]:
    cur = await _conn().execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_user(user_id: int, username: str, full_name: str) -> None:
    await _conn().execute(
        """INSERT INTO users (user_id, username, full_name, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
             username = excluded.username,
             full_name = excluded.full_name,
             updated_at = excluded.updated_at""",
        (user_id, username, full_name, now(), now()),
    )
    await _conn().commit()


async def all_user_ids(only_active: bool = False) -> list[int]:
    q = "SELECT user_id FROM users"
    if only_active:
        q += " WHERE banned = 0"
    cur = await _conn().execute(q)
    return [r[0] for r in await cur.fetchall()]


async def count_users() -> int:
    cur = await _conn().execute("SELECT COUNT(*) FROM users")
    return (await cur.fetchone())[0]


async def set_banned(user_id: int, banned: bool) -> None:
    await _conn().execute("UPDATE users SET banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
    await _conn().commit()


async def set_currency(user_id: int, currency: str) -> None:
    await _conn().execute(
        "UPDATE users SET currency = ? WHERE user_id = ?", (currency, user_id)
    )
    await _conn().commit()


async def get_currency(user_id: int) -> str:
    user = await get_user(user_id)
    return user["currency"] if user else "RUB"


# ---------------------------------------------------------------- balance

async def change_balance(user_id: int, delta: float) -> None:
    await _conn().execute(
        "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
        (delta, now(), user_id),
    )
    await _conn().commit()


async def get_balance(user_id: int) -> float:
    user = await get_user(user_id)
    return float(user["balance"]) if user else 0.0


# ---------------------------------------------------------------- categories

async def create_category(parent_id: Optional[int], title: str) -> int:
    cur = await _conn().execute(
        "INSERT INTO categories (parent_id, title, created_at) VALUES (?, ?, ?)",
        (parent_id, title, now()),
    )
    await _conn().commit()
    return cur.lastrowid


async def get_category(category_id: int) -> Optional[dict]:
    cur = await _conn().execute("SELECT * FROM categories WHERE id = ?", (category_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def child_categories(parent_id: Optional[int], visible_only: bool = True) -> list[dict]:
    if parent_id is None:
        q = "SELECT * FROM categories WHERE parent_id IS NULL"
        params: tuple = ()
    else:
        q = "SELECT * FROM categories WHERE parent_id = ?"
        params = (parent_id,)
    if visible_only:
        q += " AND is_visible = 1"
    q += " ORDER BY sort_order, id"
    cur = await _conn().execute(q, params)
    return [dict(r) for r in await cur.fetchall()]


async def all_categories() -> list[dict]:
    cur = await _conn().execute("SELECT * FROM categories ORDER BY sort_order, id")
    return [dict(r) for r in await cur.fetchall()]


async def update_category(category_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    await _conn().execute(
        f"UPDATE categories SET {sets} WHERE id = ?", (*fields.values(), category_id)
    )
    await _conn().commit()


async def delete_category(category_id: int) -> None:
    await _conn().execute("DELETE FROM categories WHERE id = ?", (category_id,))
    await _conn().commit()


async def count_subcategories(parent_id: int) -> int:
    cur = await _conn().execute("SELECT COUNT(*) FROM categories WHERE parent_id = ?", (parent_id,))
    return (await cur.fetchone())[0]


async def count_products(category_id: int) -> int:
    cur = await _conn().execute("SELECT COUNT(*) FROM products WHERE category_id = ?", (category_id,))
    return (await cur.fetchone())[0]


# ---------------------------------------------------------------- products

async def create_product(category_id: int, title: str, description: str, price: float) -> int:
    cur = await _conn().execute(
        "INSERT INTO products (category_id, title, description, price, created_at) VALUES (?, ?, ?, ?, ?)",
        (category_id, title, description, price, now()),
    )
    await _conn().commit()
    return cur.lastrowid


async def get_product(product_id: int) -> Optional[dict]:
    cur = await _conn().execute("SELECT * FROM products WHERE id = ?", (product_id,))
    row = await cur.fetchone()
    return dict(row) if row else None


async def update_product(product_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    await _conn().execute(
        f"UPDATE products SET {sets} WHERE id = ?", (*fields.values(), product_id)
    )
    await _conn().commit()


async def delete_product(product_id: int) -> None:
    await _conn().execute("DELETE FROM products WHERE id = ?", (product_id,))
    await _conn().commit()


async def products_in(category_id: int, visible_only: bool = True) -> list[dict]:
    q = "SELECT * FROM products WHERE category_id = ?"
    if visible_only:
        q += " AND is_visible = 1"
    q += " ORDER BY sort_order, id"
    cur = await _conn().execute(q, (category_id,))
    return [dict(r) for r in await cur.fetchall()]


async def product_stock(product_id: int) -> int:
    cur = await _conn().execute(
        "SELECT COUNT(*) FROM product_items WHERE product_id = ? AND sold_at IS NULL", (product_id,)
    )
    return (await cur.fetchone())[0]


async def products_stock_map(category_id: Optional[int] = None) -> dict[int, int]:
    """Остатки всех товаров сразу (одним запросом): {product_id: stock}.

    category_id=None — по всем товарам; иначе — только внутри категории.
    """
    q = ("SELECT p.id AS pid, COUNT(i.id) AS stock FROM products p "
         "LEFT JOIN product_items i ON i.product_id = p.id AND i.sold_at IS NULL")
    params: tuple = ()
    if category_id is not None:
        q += " WHERE p.category_id = ?"
        params = (category_id,)
    q += " GROUP BY p.id"
    cur = await _conn().execute(q, params)
    return {row[0]: row[1] for row in await cur.fetchall()}


async def add_product_items(product_id: int, contents: list[str]) -> int:
    """Добавляет позиции для автовыдачи (каждая строка/блок = 1 позиция). Возвращает число добавленных."""
    await _conn().executemany(
        "INSERT INTO product_items (product_id, content) VALUES (?, ?)",
        [(product_id, c) for c in contents],
    )
    await _conn().commit()
    return len(contents)


async def list_product_items(product_id: int, only_available: bool = True) -> list[dict]:
    q = "SELECT * FROM product_items WHERE product_id = ?"
    if only_available:
        q += " AND sold_at IS NULL"
    cur = await _conn().execute(q, (product_id,))
    return [dict(r) for r in await cur.fetchall()]


async def delete_product_item(item_id: int) -> None:
    await _conn().execute("DELETE FROM product_items WHERE id = ?", (item_id,))
    await _conn().commit()


# ---------------------------------------------------------------- purchase (автовыдача)

async def buy_product(user_id: int, product_id: int) -> dict:
    """Атомарная покупка: списывает баланс и «продаёт» одну свободную позицию.

    Возвращает {"ok": True, ...} или {"ok": False, "error": ...}.
    """
    conn = _conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        user = await get_user(user_id)
        product = await get_product(product_id)
        if not user:
            await conn.rollback()
            return {"ok": False, "error": "user_not_found"}
        if user["banned"]:
            await conn.rollback()
            return {"ok": False, "error": "banned"}
        if not product or not product["is_visible"]:
            await conn.rollback()
            return {"ok": False, "error": "product_not_found"}

        cur = await conn.execute(
            "SELECT id, content FROM product_items WHERE product_id = ? AND sold_at IS NULL ORDER BY id LIMIT 1",
            (product_id,),
        )
        item = await cur.fetchone()
        if not item:
            await conn.rollback()
            return {"ok": False, "error": "out_of_stock"}

        if float(user["balance"]) < float(product["price"]):
            await conn.rollback()
            return {"ok": False, "error": "insufficient_funds",
                    "need": float(product["price"]) - float(user["balance"])}

        await conn.execute("UPDATE product_items SET sold_at = ? WHERE id = ?", (now(), item["id"]))
        await conn.execute(
            """UPDATE users SET balance = balance - ?, total_spent = total_spent + ?,
               purchases_cnt = purchases_cnt + 1, updated_at = ? WHERE user_id = ?""",
            (product["price"], product["price"], now(), user_id),
        )
        await conn.execute(
            """INSERT INTO purchases (user_id, product_id, product_title, price, item_content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, product_id, product["title"], product["price"], item["content"], now()),
        )
        await conn.commit()
        return {"ok": True, "content": item["content"], "price": product["price"],
                "title": product["title"]}
    except Exception:
        await conn.rollback()
        raise


# ---------------------------------------------------------------- purchases history

async def user_purchases(user_id: int, limit: int = 10) -> list[dict]:
    cur = await _conn().execute(
        "SELECT * FROM purchases WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    )
    return [dict(r) for r in await cur.fetchall()]


async def total_revenue() -> float:
    cur = await _conn().execute("SELECT COALESCE(SUM(price), 0) FROM purchases")
    return float((await cur.fetchone())[0])


async def recent_purchases(limit: int = 10) -> list[dict]:
    cur = await _conn().execute(
        "SELECT p.*, u.username, u.full_name FROM purchases p JOIN users u ON u.user_id = p.user_id ORDER BY p.id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in await cur.fetchall()]


# ---------------------------------------------------------------- deposits

async def create_deposit(user_id: int, provider: str, invoice_id: str, amount: float, credit: float) -> int:
    cur = await _conn().execute(
        """INSERT INTO deposits (user_id, provider, invoice_id, amount, credit, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, provider, invoice_id, amount, credit, now()),
    )
    await _conn().commit()
    return cur.lastrowid


async def get_deposit_by_invoice(invoice_id: str) -> Optional[dict]:
    cur = await _conn().execute(
        "SELECT * FROM deposits WHERE invoice_id = ? ORDER BY id DESC LIMIT 1", (invoice_id,)
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def mark_deposit_paid(deposit_id: int) -> bool:
    """Помечает депозит оплаченным (только один раз) и зачисляет баланс. True — если зачислили сейчас."""
    conn = _conn()
    await conn.execute("BEGIN IMMEDIATE")
    try:
        cur = await conn.execute(
            "SELECT status, user_id, credit FROM deposits WHERE id = ?", (deposit_id,)
        )
        row = await cur.fetchone()
        if not row:
            await conn.rollback()
            return False
        if row["status"] == "paid":
            await conn.rollback()
            return False
        await conn.execute(
            "UPDATE deposits SET status = 'paid', paid_at = ? WHERE id = ?", (now(), deposit_id)
        )
        await conn.execute(
            "UPDATE users SET balance = balance + ?, updated_at = ? WHERE user_id = ?",
            (row["credit"], now(), row["user_id"]),
        )
        await conn.commit()
        return True
    except Exception:
        await conn.rollback()
        raise


async def mark_deposit_status(deposit_id: int, status: str) -> None:
    await _conn().execute("UPDATE deposits SET status = ? WHERE id = ?", (status, deposit_id))
    await _conn().commit()


async def pending_deposits() -> list[dict]:
    cur = await _conn().execute("SELECT * FROM deposits WHERE status = 'pending' ORDER BY id")
    return [dict(r) for r in await cur.fetchall()]


async def deposits_stats() -> dict:
    cur = await _conn().execute(
        "SELECT COALESCE(SUM(credit), 0) FROM deposits WHERE status = 'paid'"
    )
    paid = float((await cur.fetchone())[0])
    cur = await _conn().execute("SELECT COUNT(*) FROM deposits WHERE status = 'pending'")
    pending = (await cur.fetchone())[0]
    return {"paid_sum": paid, "pending_cnt": pending}


# ---------------------------------------------------------------- settings

async def get_setting(key: str, default: str = "") -> str:
    cur = await _conn().execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = await cur.fetchone()
    return row[0] if row else default


async def set_setting(key: str, value: str) -> None:
    await _conn().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await _conn().commit()


# ---------------------------------------------------------------- broadcast

async def save_broadcast_state(text: str, photo: Optional[str], keyboard_json: Optional[str],
                               total: int) -> None:
    await _conn().execute(
        """INSERT INTO broadcast_state (id, text, photo, keyboard_json, total, sent, failed, updated_at)
           VALUES (1, ?, ?, ?, ?, 0, 0, ?)
           ON CONFLICT(id) DO UPDATE SET text = excluded.text, photo = excluded.photo,
              keyboard_json = excluded.keyboard_json, total = excluded.total,
              sent = 0, failed = 0, updated_at = excluded.updated_at""",
        (text, photo, keyboard_json, total, now()),
    )
    await _conn().commit()


async def get_broadcast_state() -> Optional[dict]:
    cur = await _conn().execute("SELECT * FROM broadcast_state WHERE id = 1")
    row = await cur.fetchone()
    return dict(row) if row else None


async def update_broadcast_progress(sent: int, failed: int) -> None:
    await _conn().execute(
        "UPDATE broadcast_state SET sent = ?, failed = ?, updated_at = ? WHERE id = 1",
        (sent, failed, now()),
    )
    await _conn().commit()


async def clear_broadcast_state() -> None:
    await _conn().execute("DELETE FROM broadcast_state WHERE id = 1")
    await _conn().commit()


# ---------------------------------------------------------------- misc

def random_code(length: int = 8) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def parse_amount(text: str) -> Optional[float]:
    try:
        val = float(str(text).strip().replace(",", "."))
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def parse_int(text: str) -> Optional[int]:
    try:
        return int(str(text).strip())
    except (ValueError, TypeError):
        return None


def load_keyboard_json(text_json: str) -> Optional[list]:
    try:
        data = json.loads(text_json)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None

# db.py
# SQLite schema + helpers. When USE_POSTGRES=1, init_db() delegates to db_pg.

import aiosqlite
from contextlib import asynccontextmanager
from config import DB_PATH, USE_POSTGRES

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL,
    password      TEXT NOT NULL,
    ovpn_user     TEXT NOT NULL,
    ovpn_pass     TEXT NOT NULL,
    plan          TEXT NOT NULL,
    country       TEXT DEFAULT 'UNKNOWN',
    expire_date   TEXT NOT NULL,
    days_left     INTEGER NOT NULL DEFAULT 0,
    auto_renew    INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'ACTIVE',
    license       TEXT,
    pptp          TEXT,
    raw_line      TEXT NOT NULL,
    sold_to       INTEGER,
    sold_at       TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_acc_status_plan ON accounts(status, plan);
CREATE INDEX IF NOT EXISTS idx_acc_country    ON accounts(country);

CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    language      TEXT DEFAULT 'bn',
    balance_bdt   INTEGER DEFAULT 0,
    balance_stars INTEGER DEFAULT 0,
    is_banned     INTEGER DEFAULT 0,
    is_premium    INTEGER DEFAULT 0,
    referred_by   INTEGER,
    ref_count     INTEGER DEFAULT 0,
    joined_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen     TEXT
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    account_id   INTEGER NOT NULL,
    plan         TEXT NOT NULL,
    payment      TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    status       TEXT NOT NULL,
    txn_ref      TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    delivered_at TEXT,
    FOREIGN KEY(account_id) REFERENCES accounts(id)
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, status);

CREATE TABLE IF NOT EXISTS promos (
    code         TEXT PRIMARY KEY,
    discount_pct INTEGER NOT NULL,
    max_uses     INTEGER DEFAULT 0,
    used         INTEGER DEFAULT 0,
    expires_at   TEXT,
    active       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS referrals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    referrer_id  INTEGER NOT NULL,
    referee_id   INTEGER NOT NULL UNIQUE,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    eligible_at  TEXT,
    paid         INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tickets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    order_id     INTEGER,
    subject      TEXT,
    body         TEXT,
    status       TEXT DEFAULT 'open',
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS warranty (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    order_id     INTEGER NOT NULL,
    reason       TEXT,
    status       TEXT DEFAULT 'pending',
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stock_alerts (
    user_id      INTEGER NOT NULL,
    plan         TEXT NOT NULL,
    PRIMARY KEY(user_id, plan)
);
"""


async def init_db():
    if USE_POSTGRES:
        from db_pg import init_pool
        await init_pool()
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


@asynccontextmanager
async def get_db():
    """Backend-agnostic DB context. Handlers use this exclusively."""
    if USE_POSTGRES:
        from db_pg import connect as pg_connect
        async with pg_connect() as conn:
            yield conn
    else:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            yield db


def sql(q: str) -> str:
    """Rewrite '?' placeholders to '%s' for Postgres. No-op on SQLite."""
    if USE_POSTGRES:
        return q.replace("?", "%s")
    return q
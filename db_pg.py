# db_pg.py
# Postgres pool + schema. Only used when RBX404_USE_PG=1.

import os
from contextlib import asynccontextmanager
import psycopg
from psycopg_pool import AsyncConnectionPool
from db import HybridRow


def _hybrid_row_factory(cursor):
    """psycopg row_factory producing HybridRow instead of dict_row, so
    every `row[0]` / `for a, b in rows` call site written for SQLite's
    aiosqlite.Row keeps working when RBX404_USE_PG=1."""
    cols = [c.name for c in cursor.description]

    def make_row(values):
        return HybridRow(cols, values)

    return make_row

PG_DSN = os.getenv("RBX404_PG_DSN", "")
_pool: AsyncConnectionPool | None = None

SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS accounts (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL,
    password      TEXT NOT NULL,
    ovpn_user     TEXT NOT NULL,
    ovpn_pass     TEXT NOT NULL,
    plan          TEXT NOT NULL,
    country       TEXT DEFAULT 'UNKNOWN',
    expire_date   DATE NOT NULL,
    days_left     INTEGER NOT NULL DEFAULT 0,
    auto_renew    BOOLEAN DEFAULT FALSE,
    status        TEXT DEFAULT 'ACTIVE',
    license       TEXT,
    pptp          TEXT,
    raw_line      TEXT NOT NULL,
    sold_to       BIGINT,
    sold_at       TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_acc_status_plan ON accounts(status, plan);
CREATE INDEX IF NOT EXISTS idx_acc_country    ON accounts(country);

CREATE TABLE IF NOT EXISTS users (
    user_id       BIGINT PRIMARY KEY,
    username      TEXT,
    first_name    TEXT,
    language      TEXT DEFAULT 'bn',
    balance_bdt   INTEGER DEFAULT 0,
    balance_stars INTEGER DEFAULT 0,
    is_banned     BOOLEAN DEFAULT FALSE,
    is_premium    BOOLEAN DEFAULT FALSE,
    referred_by   BIGINT,
    ref_count     INTEGER DEFAULT 0,
    joined_at     TIMESTAMPTZ DEFAULT NOW(),
    last_seen     TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS orders (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    account_id   INTEGER NOT NULL REFERENCES accounts(id),
    plan         TEXT NOT NULL,
    payment      TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    status       TEXT NOT NULL,
    txn_ref      TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    delivered_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, status);

CREATE TABLE IF NOT EXISTS promos (
    code         TEXT PRIMARY KEY,
    discount_pct INTEGER NOT NULL,
    max_uses     INTEGER DEFAULT 0,
    used         INTEGER DEFAULT 0,
    expires_at   DATE,
    active       BOOLEAN DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS referrals (
    id           SERIAL PRIMARY KEY,
    referrer_id  BIGINT NOT NULL,
    referee_id   BIGINT UNIQUE NOT NULL,
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    eligible_at  TIMESTAMPTZ,
    paid         BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS tickets (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    order_id     INTEGER,
    subject      TEXT,
    body         TEXT,
    status       TEXT DEFAULT 'open',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS warranty (
    id           SERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    order_id     INTEGER NOT NULL,
    reason       TEXT,
    status       TEXT DEFAULT 'pending',
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stock_alerts (
    user_id      BIGINT NOT NULL,
    plan         TEXT NOT NULL,
    PRIMARY KEY(user_id, plan)
);
"""


async def init_pool():
    global _pool
    if not PG_DSN:
        raise RuntimeError("RBX404_PG_DSN missing")
    _pool = AsyncConnectionPool(PG_DSN, min_size=2, max_size=10, open=False)
    await _pool.open()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SCHEMA_PG)
        await conn.commit()


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def connect():
    if not _pool:
        raise RuntimeError("PG pool not initialized")
    async with _pool.connection() as conn:
        conn.row_factory = _hybrid_row_factory
        yield conn
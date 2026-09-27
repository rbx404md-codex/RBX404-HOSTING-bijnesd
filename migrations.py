# migrations.py
# Small ordered migration runner. Each migration runs at most once,
# tracked in schema_migrations(version) — safe to re-run init_db() on
# every deploy without re-applying or destroying anything.

import logging
from db import get_db, sql as qs
from config import USE_POSTGRES

log = logging.getLogger("rbx404.migrations")


async def _ensure_table(db):
    if USE_POSTGRES:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    await db.commit()


async def _001_reservation_lifecycle(db):
    """accounts: add reserved_by / reserved_at / reservation_expires_at so
    a checkout that's abandoned mid-flow can expire back to ACTIVE instead
    of being stranded as sold-to-nobody forever."""
    if USE_POSTGRES:
        await db.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reserved_by BIGINT")
        await db.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reserved_at TIMESTAMPTZ")
        await db.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS reservation_expires_at TIMESTAMPTZ")
    else:
        cur = await db.execute("PRAGMA table_info(accounts)")
        cols = {r[1] for r in await cur.fetchall()}
        if "reserved_by" not in cols:
            await db.execute("ALTER TABLE accounts ADD COLUMN reserved_by INTEGER")
        if "reserved_at" not in cols:
            await db.execute("ALTER TABLE accounts ADD COLUMN reserved_at TEXT")
        if "reservation_expires_at" not in cols:
            await db.execute("ALTER TABLE accounts ADD COLUMN reservation_expires_at TEXT")


async def _002_wallet_ledger(db):
    """A real wallet ledger — every balance change gets a row here, so
    balance_bdt/balance_stars on `users` are never modified silently."""
    if USE_POSTGRES:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id             SERIAL PRIMARY KEY,
                user_id        BIGINT NOT NULL,
                type           TEXT NOT NULL,
                currency       TEXT NOT NULL,
                amount         INTEGER NOT NULL,
                balance_before INTEGER NOT NULL,
                balance_after  INTEGER NOT NULL,
                reference      TEXT,
                description    TEXT,
                admin_id       BIGINT,
                created_at     TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                type           TEXT NOT NULL,
                currency       TEXT NOT NULL,
                amount         INTEGER NOT NULL,
                balance_before INTEGER NOT NULL,
                balance_after  INTEGER NOT NULL,
                reference      TEXT,
                description    TEXT,
                admin_id       INTEGER,
                created_at     TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    await db.execute(qs(
        "CREATE INDEX IF NOT EXISTS idx_wallet_tx_user ON wallet_transactions(user_id)"
    ))


async def _003_audit_log(db):
    """Admin-action audit trail (approvals, rejections, warranty decisions,
    promo/broadcast actions) — who did what, when, to what."""
    if USE_POSTGRES:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id         SERIAL PRIMARY KEY,
                admin_id   BIGINT,
                action     TEXT NOT NULL,
                target     TEXT,
                detail     TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id   INTEGER,
                action     TEXT NOT NULL,
                target     TEXT,
                detail     TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


async def _004_promo_engine(db):
    """promos: real constraints (plan restriction, min order, per-user
    limit, first-order-only) plus promo_redemptions for usage history and
    enforcing per-user limits — none of this existed before; /promo CODE
    was accepted from users but never validated or applied to any price."""
    if USE_POSTGRES:
        await db.execute("ALTER TABLE promos ADD COLUMN IF NOT EXISTS plan_restriction TEXT")
        await db.execute("ALTER TABLE promos ADD COLUMN IF NOT EXISTS min_order_bdt INTEGER DEFAULT 0")
        await db.execute("ALTER TABLE promos ADD COLUMN IF NOT EXISTS per_user_limit INTEGER DEFAULT 1")
        await db.execute("ALTER TABLE promos ADD COLUMN IF NOT EXISTS first_order_only BOOLEAN DEFAULT FALSE")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                id           SERIAL PRIMARY KEY,
                code         TEXT NOT NULL,
                user_id      BIGINT NOT NULL,
                order_id     INTEGER,
                bdt_before   INTEGER NOT NULL,
                bdt_after    INTEGER NOT NULL,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    else:
        cur = await db.execute("PRAGMA table_info(promos)")
        cols = {r[1] for r in await cur.fetchall()}
        if "plan_restriction" not in cols:
            await db.execute("ALTER TABLE promos ADD COLUMN plan_restriction TEXT")
        if "min_order_bdt" not in cols:
            await db.execute("ALTER TABLE promos ADD COLUMN min_order_bdt INTEGER DEFAULT 0")
        if "per_user_limit" not in cols:
            await db.execute("ALTER TABLE promos ADD COLUMN per_user_limit INTEGER DEFAULT 1")
        if "first_order_only" not in cols:
            await db.execute("ALTER TABLE promos ADD COLUMN first_order_only INTEGER DEFAULT 0")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                code         TEXT NOT NULL,
                user_id      INTEGER NOT NULL,
                order_id     INTEGER,
                bdt_before   INTEGER NOT NULL,
                bdt_after    INTEGER NOT NULL,
                created_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
    await db.execute(qs(
        "CREATE INDEX IF NOT EXISTS idx_promo_redemptions_user ON promo_redemptions(code, user_id)"
    ))


MIGRATIONS = [
    (1, "reservation_lifecycle_columns", _001_reservation_lifecycle),
    (2, "wallet_ledger", _002_wallet_ledger),
    (3, "audit_log", _003_audit_log),
    (4, "promo_engine", _004_promo_engine),
]


async def run_migrations():
    async with get_db() as db:
        await _ensure_table(db)
        cur = await db.execute(qs("SELECT version FROM schema_migrations"))
        applied = {r[0] for r in await cur.fetchall()}
        for version, name, fn in MIGRATIONS:
            if version in applied:
                continue
            log.info("applying migration %s: %s", version, name)
            await fn(db)
            await db.execute(
                qs("INSERT INTO schema_migrations(version, name) VALUES (?, ?)"),
                (version, name),
            )
            await db.commit()

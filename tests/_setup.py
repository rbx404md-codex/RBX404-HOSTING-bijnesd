# tests/_setup.py
# Shared helper for pointing the app's DB layer at a throwaway SQLite
# file for tests, instead of the real ./data/rbx404.sqlite3.
#
# Must run before `import db` (or anything that imports db) anywhere in
# the process, because config.py / db.py read RBX404_DB once at import
# time. Each test module calls init_test_db() in its own setUpModule().

import os
import tempfile
import asyncio


def use_temp_db():
    """Point the DB layer at a fresh temp file. Call this before
    importing db/inventory/promos/wallet/migrations for the first time
    in a given test process."""
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.environ["RBX404_DB"] = path
    os.environ["RBX404_USE_PG"] = "0"
    return path


async def reset_tables(db_module):
    """Wipe rows between tests without re-creating the schema/migrations
    (which is comparatively slow and unnecessary per-test)."""
    async with db_module.get_db() as db:
        for table in ("accounts", "orders", "users", "wallet_transactions",
                      "promos", "promo_redemptions", "referrals", "audit_logs"):
            try:
                await db.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        await db.commit()


async def seed_account(db_module, sql_module, plan="1mo", country="US",
                        expire_date="2099-01-01") -> int:
    """Insert one ACTIVE account for tests to reserve/finalize/release."""
    async with db_module.get_db() as db:
        cur = await db.execute(sql_module("""
            INSERT INTO accounts(email, password, ovpn_user, ovpn_pass, plan,
                                 country, expire_date, raw_line, status)
            VALUES (?,?,?,?,?,?,?,?, 'ACTIVE')
        """), (f"test-{os.urandom(4).hex()}@example.com", "pw", "ou", "op",
               plan, country, expire_date, "raw"))
        await db.commit()
        return cur.lastrowid


async def seed_user(db_module, sql_module, user_id: int):
    async with db_module.get_db() as db:
        await db.execute(sql_module(
            "INSERT INTO users(user_id, balance_bdt, balance_stars) VALUES (?,0,0)"
        ), (user_id,))
        await db.commit()

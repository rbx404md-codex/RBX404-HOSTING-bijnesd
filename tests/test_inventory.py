import unittest
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._setup import use_temp_db
use_temp_db()

import db  # noqa: E402
from db import sql as qs  # noqa: E402
import inventory  # noqa: E402
import jobs  # noqa: E402
from tests._setup import reset_tables, seed_account  # noqa: E402


class TestInventory(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        asyncio.run(db.init_db())

    async def asyncSetUp(self):
        await reset_tables(db)

    async def test_reserve_one_returns_none_when_no_stock(self):
        acct = await inventory.reserve_one("1mo", user_id=1)
        self.assertIsNone(acct)

    async def test_reserve_one_claims_and_marks_reserved(self):
        acc_id = await seed_account(db, qs, plan="1mo")
        acct = await inventory.reserve_one("1mo", user_id=42)
        self.assertIsNotNone(acct)
        self.assertEqual(acct["id"], acc_id)
        self.assertEqual(acct["status"], "RESERVED")
        # A second reservation attempt must find no ACTIVE stock left.
        again = await inventory.reserve_one("1mo", user_id=43)
        self.assertIsNone(again)

    async def test_concurrent_reservation_never_double_sells(self):
        """The bug this guards against: two buyers racing for the same
        single account must not both be told they got it."""
        await seed_account(db, qs, plan="1mo")
        results = await asyncio.gather(
            inventory.reserve_one("1mo", user_id=1),
            inventory.reserve_one("1mo", user_id=2),
        )
        winners = [r for r in results if r is not None]
        self.assertEqual(len(winners), 1, "exactly one caller should win the race")

    async def test_release_reserved_clears_reservation_fields(self):
        await seed_account(db, qs, plan="1mo")
        acct = await inventory.reserve_one("1mo", user_id=1)
        await inventory.release_reserved(acct["id"])
        async with db.get_db() as conn:
            cur = await conn.execute(qs("SELECT * FROM accounts WHERE id=?"), (acct["id"],))
            row = dict(await cur.fetchone())
        self.assertEqual(row["status"], "ACTIVE")
        self.assertIsNone(row["reserved_by"])
        self.assertIsNone(row["reservation_expires_at"])

    async def test_finalize_sale_succeeds_on_reserved(self):
        await seed_account(db, qs, plan="1mo")
        acct = await inventory.reserve_one("1mo", user_id=7)
        ok = await inventory.finalize_sale(acct["id"], 7)
        self.assertTrue(ok)
        async with db.get_db() as conn:
            cur = await conn.execute(qs("SELECT status, sold_to FROM accounts WHERE id=?"),
                                     (acct["id"],))
            row = dict(await cur.fetchone())
        self.assertEqual(row["status"], "SOLD")
        self.assertEqual(row["sold_to"], 7)

    async def test_finalize_sale_fails_if_not_reserved(self):
        acc_id = await seed_account(db, qs, plan="1mo")  # left ACTIVE, never reserved
        ok = await inventory.finalize_sale(acc_id, 7)
        self.assertFalse(ok)

    async def test_finalize_sale_fails_twice_in_a_row(self):
        """Guards against double delivery: finalizing an already-SOLD
        account a second time must not silently succeed."""
        await seed_account(db, qs, plan="1mo")
        acct = await inventory.reserve_one("1mo", user_id=7)
        self.assertTrue(await inventory.finalize_sale(acct["id"], 7))
        self.assertFalse(await inventory.finalize_sale(acct["id"], 7))

    async def test_expired_reservation_is_released_by_job(self):
        await seed_account(db, qs, plan="1mo")
        acct = await inventory.reserve_one("1mo", user_id=1, reservation_minutes=-1)
        # reservation_minutes=-1 -> reservation_expires_at is already in
        # the past, simulating an abandoned checkout.
        class _Ctx:
            pass
        await jobs.job_release_expired_reservations(_Ctx())
        async with db.get_db() as conn:
            cur = await conn.execute(qs("SELECT status FROM accounts WHERE id=?"), (acct["id"],))
            row = dict(await cur.fetchone())
        self.assertEqual(row["status"], "ACTIVE")


if __name__ == "__main__":
    unittest.main()

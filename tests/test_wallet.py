import unittest
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._setup import use_temp_db
use_temp_db()

import db  # noqa: E402
from db import sql as qs  # noqa: E402
import wallet  # noqa: E402
from tests._setup import reset_tables, seed_user  # noqa: E402


class TestWallet(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        asyncio.run(db.init_db())

    async def asyncSetUp(self):
        await reset_tables(db)
        await seed_user(db, qs, 1)

    async def test_credit_increases_balance_and_logs_ledger(self):
        after = await wallet.credit(1, "stars", 10, "test_credit")
        self.assertEqual(after, 10)
        hist = await wallet.history(1)
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["amount"], 10)
        self.assertEqual(hist[0]["balance_before"], 0)
        self.assertEqual(hist[0]["balance_after"], 10)

    async def test_debit_with_sufficient_funds(self):
        await wallet.credit(1, "stars", 20, "seed")
        after = await wallet.debit(1, "stars", 5, "spend")
        self.assertEqual(after, 15)

    async def test_debit_with_insufficient_funds_returns_none_and_unchanged(self):
        await wallet.credit(1, "stars", 5, "seed")
        result = await wallet.debit(1, "stars", 100, "spend")
        self.assertIsNone(result)
        async with db.get_db() as conn:
            cur = await conn.execute(qs("SELECT balance_stars FROM users WHERE user_id=?"), (1,))
            balance = (await cur.fetchone())[0]
        self.assertEqual(balance, 5)  # untouched

    async def test_invalid_currency_rejected(self):
        with self.assertRaises(ValueError):
            await wallet.credit(1, "euros", 10, "test")

    async def test_non_positive_amount_rejected(self):
        with self.assertRaises(ValueError):
            await wallet.credit(1, "stars", 0, "test")
        with self.assertRaises(ValueError):
            await wallet.credit(1, "stars", -5, "test")


if __name__ == "__main__":
    unittest.main()

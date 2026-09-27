import unittest
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._setup import use_temp_db
use_temp_db()

import db  # noqa: E402
from db import sql as qs  # noqa: E402
import promos  # noqa: E402
from tests._setup import reset_tables  # noqa: E402


async def make_promo(**overrides):
    defaults = dict(code="SAVE10", discount_pct=10, max_uses=0, expires_at=None,
                     min_order_bdt=0, per_user_limit=1, plan_restriction=None,
                     first_order_only=0, active=1)
    defaults.update(overrides)
    async with db.get_db() as conn:
        await conn.execute(qs("""
            INSERT INTO promos(code, discount_pct, max_uses, expires_at, min_order_bdt,
                               per_user_limit, plan_restriction, first_order_only, active)
            VALUES (?,?,?,?,?,?,?,?,?)
        """), (defaults["code"], defaults["discount_pct"], defaults["max_uses"],
               defaults["expires_at"], defaults["min_order_bdt"], defaults["per_user_limit"],
               defaults["plan_restriction"], defaults["first_order_only"], defaults["active"]))
        await conn.commit()


class TestPromoEngine(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        asyncio.run(db.init_db())

    async def asyncSetUp(self):
        await reset_tables(db)

    async def test_unknown_code_rejected(self):
        r = await promos.validate_and_price("NOPE", 1, "1mo", 500, 100)
        self.assertFalse(r["ok"])

    async def test_valid_code_applies_discount(self):
        await make_promo(code="SAVE10", discount_pct=10)
        r = await promos.validate_and_price("save10", 1, "1mo", 500, 100)
        self.assertTrue(r["ok"])
        self.assertEqual(r["bdt"], 450)
        self.assertEqual(r["stars"], 90)

    async def test_inactive_code_rejected(self):
        await make_promo(code="OFF", active=0)
        r = await promos.validate_and_price("OFF", 1, "1mo", 500, 100)
        self.assertFalse(r["ok"])

    async def test_expired_code_rejected(self):
        await make_promo(code="OLD", expires_at="2000-01-01 00:00:00")
        r = await promos.validate_and_price("OLD", 1, "1mo", 500, 100)
        self.assertFalse(r["ok"])

    async def test_max_uses_enforced(self):
        await make_promo(code="LIMITED", max_uses=1)
        await promos.record_redemption("LIMITED", 1, 500, 450)
        r = await promos.validate_and_price("LIMITED", 2, "1mo", 500, 100)
        self.assertFalse(r["ok"])

    async def test_per_user_limit_enforced(self):
        await make_promo(code="ONEUSE", per_user_limit=1)
        await promos.record_redemption("ONEUSE", 1, 500, 450)
        # Same user again -> blocked; a different user should still pass.
        r_same = await promos.validate_and_price("ONEUSE", 1, "1mo", 500, 100)
        r_other = await promos.validate_and_price("ONEUSE", 2, "1mo", 500, 100)
        self.assertFalse(r_same["ok"])
        self.assertTrue(r_other["ok"])

    async def test_min_order_enforced(self):
        await make_promo(code="BIGORDER", min_order_bdt=1000)
        r = await promos.validate_and_price("BIGORDER", 1, "1mo", 500, 100)
        self.assertFalse(r["ok"])

    async def test_plan_restriction_enforced(self):
        await make_promo(code="ANNUALONLY", plan_restriction="1y")
        r_wrong = await promos.validate_and_price("ANNUALONLY", 1, "1mo", 500, 100)
        r_right = await promos.validate_and_price("ANNUALONLY", 1, "1y", 500, 100)
        self.assertFalse(r_wrong["ok"])
        self.assertTrue(r_right["ok"])

    async def test_admin_input_range_validation(self):
        self.assertIsNotNone(promos.validate_admin_input(0, 0, 0, 1))    # 0% invalid
        self.assertIsNotNone(promos.validate_admin_input(95, 0, 0, 1))   # >90% invalid
        self.assertIsNotNone(promos.validate_admin_input(10, -1, 0, 1))  # negative max_uses
        self.assertIsNone(promos.validate_admin_input(10, 100, 500, 1))  # valid


if __name__ == "__main__":
    unittest.main()

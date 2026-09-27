import unittest
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests._setup import use_temp_db
use_temp_db()

import db  # noqa: E402
from db import sql as qs  # noqa: E402
import jobs  # noqa: E402
from tests._setup import reset_tables, seed_user  # noqa: E402


class _FakeBot:
    async def send_message(self, *a, **k):
        pass


class _FakeCtx:
    def __init__(self):
        self.bot = _FakeBot()


class TestReferralPayout(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        asyncio.run(db.init_db())

    async def asyncSetUp(self):
        await reset_tables(db)
        await seed_user(db, qs, 100)  # referrer
        await seed_user(db, qs, 200)  # referee
        async with db.get_db() as conn:
            await conn.execute(qs("""
                INSERT INTO referrals(referrer_id, referee_id, paid, eligible_at)
                VALUES (?,?,0,'2000-01-01 00:00:00')
            """), (100, 200))
            await conn.commit()

    async def test_referral_paid_exactly_once_even_if_job_runs_concurrently(self):
        """The bug this guards against: two overlapping job runs (e.g. the
        scheduled tick plus an admin /runjob) must not both credit the
        same referral."""
        ctx1, ctx2 = _FakeCtx(), _FakeCtx()
        await asyncio.gather(
            jobs.job_release_referrals(ctx1),
            jobs.job_release_referrals(ctx2),
        )
        async with db.get_db() as conn:
            cur = await conn.execute(qs("SELECT balance_stars FROM users WHERE user_id=?"), (100,))
            balance = (await cur.fetchone())[0]
        from config import REFERRAL_STARS
        self.assertEqual(balance, REFERRAL_STARS, "referral must be paid exactly once, not twice")

    async def test_banned_referee_blocks_payout(self):
        async with db.get_db() as conn:
            await conn.execute(qs("UPDATE users SET is_banned=1 WHERE user_id=?"), (200,))
            await conn.commit()
        await jobs.job_release_referrals(_FakeCtx())
        async with db.get_db() as conn:
            cur = await conn.execute(qs("SELECT balance_stars FROM users WHERE user_id=?"), (100,))
            balance = (await cur.fetchone())[0]
        self.assertEqual(balance, 0)


if __name__ == "__main__":
    unittest.main()

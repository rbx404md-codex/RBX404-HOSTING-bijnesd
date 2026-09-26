# jobs.py
# Scheduled tasks.

import logging
from time import time
from datetime import datetime, timezone
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS, REFERRAL_STARS, PRICING
from db import get_db, sql as qs

log = logging.getLogger("rbx404.jobs")
LOW_STOCK_THRESHOLD = 3
_LOW_STOCK_LAST_ALERT = 0.0


async def job_release_referrals(ctx: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db() as db:
        cur = await db.execute(qs("""
            SELECT id, referrer_id, referee_id FROM referrals
            WHERE paid=0 AND eligible_at IS NOT NULL AND eligible_at <= ?
        """), (now,))
        pending = [dict(r) for r in await cur.fetchall()]

    for ref in pending:
        async with get_db() as db:
            cur = await db.execute(qs("SELECT is_banned FROM users WHERE user_id=?"),
                                   (ref["referee_id"],))
            row = await cur.fetchone()
        if not row or row[0] in (1, True):
            continue
        async with get_db() as db:
            await db.execute(qs(
                "UPDATE users SET balance_stars = balance_stars + ? WHERE user_id=?"
            ), (REFERRAL_STARS, ref["referrer_id"]))
            await db.execute(qs("UPDATE referrals SET paid=1 WHERE id=?"), (ref["id"],))
            await db.execute(qs(
                "UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?"
            ), (ref["referrer_id"],))
            await db.commit()
        try:
            await ctx.bot.send_message(
                ref["referrer_id"],
                f"🎁 Referral bonus credited: +{REFERRAL_STARS}⭐",
            )
        except Exception as e:
            log.warning("ref bonus notify failed: %s", e)


async def job_low_stock(ctx: ContextTypes.DEFAULT_TYPE):
    global _LOW_STOCK_LAST_ALERT
    now = time()
    if now - _LOW_STOCK_LAST_ALERT < 3 * 3600:
        return
    async with get_db() as db:
        cur = await db.execute(qs(
            "SELECT plan, COUNT(*) FROM accounts WHERE status='ACTIVE' GROUP BY plan"
        ))
        stock = {p: c for p, c in await cur.fetchall()}
    low = [p for p in PRICING if stock.get(p, 0) < LOW_STOCK_THRESHOLD]
    if not low:
        return
    _LOW_STOCK_LAST_ALERT = now
    text = "📉 <b>Low stock</b>\n" + "\n".join(
        f"• {PRICING[p]['label']}: {stock.get(p, 0)} left" for p in low
    )
    for admin in ADMIN_IDS:
        try:
            await ctx.bot.send_message(admin, text, parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def job_daily_digest(ctx: ContextTypes.DEFAULT_TYPE):
    async with get_db() as db:
        cur = await db.execute(qs("""
            SELECT payment, COUNT(*) AS n, COALESCE(SUM(amount),0) AS sum_amt
            FROM orders WHERE status='delivered' AND date(delivered_at)=date('now')
            GROUP BY payment
        """))
        per_rail = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(qs("""
            SELECT plan, COUNT(*) AS n FROM orders
            WHERE status='delivered' AND date(delivered_at)=date('now')
            GROUP BY plan
        """))
        per_plan = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(qs("SELECT COUNT(*) FROM users WHERE date(last_seen)=date('now')"))
        active = (await cur.fetchone())[0]
        cur = await db.execute(qs("SELECT COUNT(*) FROM accounts WHERE status='ACTIVE'"))
        stock = (await cur.fetchone())[0]

    lines = ["📊 <b>Daily digest</b>"]
    for r in per_rail:
        unit = "⭐" if r["payment"] == "stars" else "৳"
        lines.append(f"• {r['payment']}: {r['n']} · {r['sum_amt']}{unit}")
    if per_plan:
        lines.append("— by plan —")
        for r in per_plan:
            lines.append(f"• {r['plan']}: {r['n']}")
    lines.append(f"\n👥 Active today: {active}\n📦 Stock: {stock}")
    txt = "\n".join(lines)
    for admin in ADMIN_IDS:
        try:
            await ctx.bot.send_message(admin, txt, parse_mode=ParseMode.HTML)
        except Exception:
            pass


async def job_expiry_sweep(ctx: ContextTypes.DEFAULT_TYPE):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with get_db() as db:
        await db.execute(qs(
            "UPDATE accounts SET status='DEAD' WHERE status='ACTIVE' AND expire_date < ?"
        ), (today,))
        await db.commit()


async def job_backup(ctx: ContextTypes.DEFAULT_TYPE):
    from inventory import backup_db
    try:
        backup_db()
    except Exception as e:
        log.warning("backup failed: %s", e)

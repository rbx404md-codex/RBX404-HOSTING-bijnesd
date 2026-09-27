# jobs.py
# Scheduled tasks.

import logging
from time import time
from datetime import datetime, timezone
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS, REFERRAL_STARS, PRICING
from db import get_db, sql as qs
from wallet import credit

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
            # Claim this referral atomically before paying out — if two
            # job runs overlap (a scheduled run plus an admin /runjob),
            # only the one that actually flips paid 0->1 pays the
            # referrer; the loser sees rowcount 0 and skips, so the same
            # referral can never be paid twice.
            upd = await db.execute(
                qs("UPDATE referrals SET paid=1 WHERE id=? AND paid=0"), (ref["id"],)
            )
            await db.commit()
            if upd.rowcount != 1:
                continue
            await db.execute(qs(
                "UPDATE users SET ref_count = ref_count + 1 WHERE user_id=?"
            ), (ref["referrer_id"],))
            await db.commit()

        await credit(ref["referrer_id"], "stars", REFERRAL_STARS, "referral_bonus",
                    reference=f"referral:{ref['id']}",
                    description=f"Referral bonus for referee {ref['referee_id']}")
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
    from config import USE_POSTGRES
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # date('now') / date(col)='date(now)' is SQLite-only syntax — it does
    # not work against Postgres's TIMESTAMPTZ columns. Compare against an
    # explicit today string instead, cast on the Postgres side so the
    # comparison is unambiguous regardless of column type.
    today_eq = "date(delivered_at) = ?::date" if USE_POSTGRES else "date(delivered_at) = ?"
    seen_eq  = "date(last_seen) = ?::date" if USE_POSTGRES else "date(last_seen) = ?"
    async with get_db() as db:
        cur = await db.execute(qs(f"""
            SELECT payment, COUNT(*) AS n, COALESCE(SUM(amount),0) AS sum_amt
            FROM orders WHERE status='delivered' AND {today_eq}
            GROUP BY payment
        """), (today,))
        per_rail = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(qs(f"""
            SELECT plan, COUNT(*) AS n FROM orders
            WHERE status='delivered' AND {today_eq}
            GROUP BY plan
        """), (today,))
        per_plan = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(qs(f"SELECT COUNT(*) FROM users WHERE {seen_eq}"), (today,))
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


async def job_release_expired_reservations(ctx: ContextTypes.DEFAULT_TYPE):
    """RESERVED accounts whose reservation_expires_at has passed go back
    to ACTIVE. Without this, an abandoned checkout (user reserves a plan
    then never pays or cancels) permanently removed that account from
    stock — RBX404_RESERVATION_MINUTES controls the timeout."""
    from config import USE_POSTGRES
    async with get_db() as db:
        if USE_POSTGRES:
            q = ("UPDATE accounts SET status='ACTIVE', reserved_by=NULL, "
                 "reserved_at=NULL, reservation_expires_at=NULL "
                 "WHERE status='RESERVED' AND reservation_expires_at < NOW()")
        else:
            q = ("UPDATE accounts SET status='ACTIVE', reserved_by=NULL, "
                 "reserved_at=NULL, reservation_expires_at=NULL "
                 "WHERE status='RESERVED' AND reservation_expires_at < datetime('now')")
        cur = await db.execute(qs(q))
        await db.commit()
        if cur.rowcount:
            log.info("released %s expired reservation(s)", cur.rowcount)


async def job_backup(ctx: ContextTypes.DEFAULT_TYPE):
    from inventory import backup_db
    try:
        backup_db()
    except Exception as e:
        log.warning("backup failed: %s", e)

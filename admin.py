# admin.py
# Admin commands + panel.

from functools import wraps
import asyncio
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import RetryAfter, Forbidden, BadRequest
from telegram.ext import ContextTypes

from config import ADMIN_IDS, PRICING
from db import get_db, sql as qs
from inventory import bulk_import, stock_by_plan
from promos import validate_and_price, validate_admin_input

log = logging.getLogger("rbx404.admin")

BROADCAST_BATCH_SIZE = 25       # Telegram's soft global limit is ~30 msg/sec
BROADCAST_BATCH_DELAY = 1.0     # seconds between batches


def admin_only(fn):
    @wraps(fn)
    async def wrap(update: Update, ctx: ContextTypes.DEFAULT_TYPE, *a, **kw):
        u = update.effective_user
        if not u or u.id not in ADMIN_IDS:
            if update.callback_query:
                await update.callback_query.answer("Admins only.", show_alert=True)
            elif update.message:
                await update.message.reply_text("Admins only.")
            return
        return await fn(update, ctx, *a, **kw)
    return wrap


def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Import", callback_data="a:import"),
         InlineKeyboardButton("📊 Stats", callback_data="a:stats")],
        [InlineKeyboardButton("💰 Prices", callback_data="a:prices"),
         InlineKeyboardButton("📢 Broadcast", callback_data="a:broadcast")],
        [InlineKeyboardButton("🎟️ Promos", callback_data="a:promos"),
         InlineKeyboardButton("🧾 Orders", callback_data="a:orders")],
        [InlineKeyboardButton("🆘 Tickets", callback_data="a:tickets"),
         InlineKeyboardButton("🔧 Warranty", callback_data="a:warranty")],
        [InlineKeyboardButton("💾 Backup", callback_data="a:backup")],
    ])


@admin_only
async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    stock = await stock_by_plan()
    total = sum(stock.values())
    async with get_db() as db:
        cur = await db.execute(qs(
            "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders "
            "WHERE date(created_at)=date('now') AND status='delivered'"
        ))
        row = await cur.fetchone()
        t_orders, t_rev = (row[0] or 0), (row[1] or 0)
        cur = await db.execute(qs("SELECT COUNT(*) FROM users"))
        users = (await cur.fetchone())[0]
    txt = (
        f"👑 <b>ADMIN PANEL</b>\n\n"
        f"📦 Inventory: {total} accounts\n"
        f"💰 Today's Sales: {t_orders} orders ({t_rev})\n"
        f"👥 Total Users: {users}\n\n"
        f"Stock: " + " · ".join(f"{k}:{v}" for k, v in stock.items())
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            txt, parse_mode=ParseMode.HTML, reply_markup=kb_admin()
        )
    else:
        await update.message.reply_text(
            txt, parse_mode=ParseMode.HTML, reply_markup=kb_admin()
        )


@admin_only
async def cmd_import(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["awaiting_import"] = True
    ctx.user_data["pending_import"] = ""
    await update.message.reply_text("📥 Paste accounts, one per line. /done to import.")


@admin_only
async def cmd_import_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    blob = ctx.user_data.pop("pending_import", "")
    ctx.user_data.pop("awaiting_import", None)
    if not blob.strip():
        await update.message.reply_text("Nothing to import.")
        return
    res = await bulk_import(blob)
    await update.message.reply_text(
        f"✅ Imported: {res['inserted']}\n♻️ Dupes: {res['dupes']}\n"
        f"⚠️ Errors: {len(res['errors'])}\n\n" +
        ("\n".join(res["errors"][:10]) if res["errors"] else "")
    )


async def collect_import(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.get("awaiting_import"):
        return False
    if update.effective_user.id not in ADMIN_IDS:
        return False
    text = update.message.text or ""
    if text.strip() == "/done":
        await cmd_import_done(update, ctx)
        return True
    ctx.user_data["pending_import"] = ctx.user_data.get("pending_import", "") + "\n" + text
    return True


@admin_only
async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Batched, rate-limited, flood-safe broadcast.

    Previously this fired ctx.bot.send_message in a tight loop with no
    delay at all — against more than ~30 recipients this trips Telegram's
    flood limits (RetryAfter errors, or the account getting rate-limited
    outright), and a Forbidden (user blocked the bot) was swallowed
    without ever cleaning up that user's is_banned flag, so every future
    broadcast would keep retrying the same dead chat forever.
    """
    if not ctx.args:
        await update.message.reply_text("Usage: /broadcast <message>")
        return
    msg = " ".join(ctx.args)
    async with get_db() as db:
        cur = await db.execute(qs("SELECT user_id FROM users WHERE is_banned=0"))
        ids = [r[0] for r in await cur.fetchall()]

    total = len(ids)
    sent = failed = blocked = 0
    status_msg = await update.message.reply_text(f"📢 Broadcasting to {total} users… 0%")

    for i, uid in enumerate(ids):
        for attempt in range(2):  # one retry after honoring RetryAfter
            try:
                await ctx.bot.send_message(uid, msg, parse_mode=ParseMode.HTML)
                sent += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 0.5)
                continue
            except Forbidden:
                blocked += 1
                async with get_db() as db:
                    await db.execute(qs("UPDATE users SET is_banned=1 WHERE user_id=?"), (uid,))
                    await db.commit()
                break
            except BadRequest as e:
                failed += 1
                log.warning("broadcast to %s failed: %s", uid, e)
                break
            except Exception as e:
                failed += 1
                log.warning("broadcast to %s failed: %s", uid, e)
                break

        if (i + 1) % BROADCAST_BATCH_SIZE == 0:
            pct = round((i + 1) / total * 100) if total else 100
            try:
                await status_msg.edit_text(f"📢 Broadcasting to {total} users… {pct}%")
            except Exception:
                pass
            await asyncio.sleep(BROADCAST_BATCH_DELAY)

    await status_msg.edit_text(
        f"📢 Done. Sent: {sent} · Blocked (removed): {blocked} · Failed: {failed}"
    )


async def cmd_promo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Dual-purpose: admins creating/updating a code use
    `/promo CODE PCT [MAX] [EXPIRY] [MIN_BDT] [PER_USER_LIMIT] [PLAN]`;
    anyone (including admins) redeeming a code for their current checkout
    uses `/promo CODE` (1 arg). Previously this whole command was
    @admin_only, so the "send /promo CODE" prompt shown to regular users
    just replied "Admins only." — redemption never actually worked.
    """
    u = update.effective_user
    is_admin = bool(u and u.id in ADMIN_IDS)

    if is_admin and len(ctx.args) >= 2:
        await _admin_create_promo(update, ctx)
        return

    if not ctx.args:
        await update.message.reply_text(
            "Usage: /promo CODE" + (" or /promo CODE PCT [MAX] [EXPIRY] [MIN_BDT] [PER_USER_LIMIT] [PLAN] (admin)" if is_admin else "")
        )
        return

    await _redeem_promo(update, ctx)


async def _admin_create_promo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        code = ctx.args[0].upper()
        pct = int(ctx.args[1])
        mx = int(ctx.args[2]) if len(ctx.args) > 2 else 0
        exp = ctx.args[3] if len(ctx.args) > 3 else None
        min_bdt = int(ctx.args[4]) if len(ctx.args) > 4 else 0
        per_user_limit = int(ctx.args[5]) if len(ctx.args) > 5 else 1
        plan_restriction = ctx.args[6] if len(ctx.args) > 6 else None
    except ValueError:
        await update.message.reply_text("PERCENT, MAX, MIN_BDT and PER_USER_LIMIT must be numbers.")
        return

    err = validate_admin_input(pct, mx, min_bdt, per_user_limit)
    if err:
        await update.message.reply_text(f"❌ {err}")
        return

    async with get_db() as db:
        await db.execute(qs("""
            INSERT INTO promos(code, discount_pct, max_uses, expires_at,
                               min_order_bdt, per_user_limit, plan_restriction)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(code) DO UPDATE SET discount_pct=excluded.discount_pct,
                max_uses=excluded.max_uses, expires_at=excluded.expires_at,
                min_order_bdt=excluded.min_order_bdt,
                per_user_limit=excluded.per_user_limit,
                plan_restriction=excluded.plan_restriction
        """), (code, pct, mx, exp, min_bdt, per_user_limit, plan_restriction))
        await db.commit()
    await update.message.reply_text(f"🎟️ Promo {code}: {pct}% off. Saved.")


async def _redeem_promo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    code = ctx.args[0]
    plan = ctx.user_data.get("plan")
    acc_id = ctx.user_data.get("reserved")
    if not (plan and acc_id):
        await update.message.reply_text("Pick a plan first with /start → 🛒 Buy VPN, then send the promo code.")
        return
    base = PRICING[plan]
    result = await validate_and_price(code, update.effective_user.id, plan,
                                      base["bdt"], base["stars"])
    if not result["ok"]:
        await update.message.reply_text(f"❌ {result['error']}")
        return
    ctx.user_data["promo_code"] = result["code"]
    ctx.user_data["promo_bdt"] = result["bdt"]
    ctx.user_data["promo_stars"] = result["stars"]
    await update.message.reply_text(
        f"✅ {result['code']} applied: {result['pct']}% off — "
        f"{result['bdt']}৳ / {result['stars']}⭐. Choose payment above to continue."
    )


@admin_only
async def cmd_runjob(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    from jobs import (
        job_release_referrals, job_low_stock, job_daily_digest,
        job_expiry_sweep, job_backup,
    )
    if not ctx.args:
        await update.message.reply_text(
            "Usage: /runjob referrals|stock|digest|expiry|backup"
        ); return
    job = ctx.args[0].lower()
    mapping = {
        "referrals": job_release_referrals,
        "stock": job_low_stock,
        "digest": job_daily_digest,
        "expiry": job_expiry_sweep,
        "backup": job_backup,
    }
    if job not in mapping:
        await update.message.reply_text("Unknown job."); return
    await update.message.reply_text(f"▶️ Running {job}…")
    await mapping[job](ctx)
    await update.message.reply_text(f"✅ {job} done.")


async def cb_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    a = q.data.split(":", 1)[1]
    if a == "stats":
        await cmd_admin(update, ctx)
    elif a == "import":
        ctx.user_data["awaiting_import"] = True
        ctx.user_data["pending_import"] = ""
        await q.edit_message_text("📥 Send the paste now. /done to finish.")
    elif a == "backup":
        from inventory import backup_db
        backup_db()
        await q.edit_message_text("💾 Backup written.")
    elif a == "broadcast":
        await q.edit_message_text("Use /broadcast <message>")
    elif a == "promos":
        await q.edit_message_text("Use /promo CODE PCT [MAX] [DATE]")
    elif a == "prices":
        txt = "\n".join(f"{k}: {v['bdt']}৳ / {v['stars']}⭐" for k, v in PRICING.items())
        await q.edit_message_text(f"💰 <b>Current prices</b>\n{txt}",
                                  parse_mode=ParseMode.HTML)
    elif a == "orders":
        from admin_views import _render_orders
        await _render_orders(q.message.chat_id, ctx, 0, edit=True, q=q)
    elif a == "tickets":
        from admin_views import _render_tickets
        await _render_tickets(q.message.chat_id, ctx, 0, edit=True, q=q)
    elif a == "warranty":
        from admin_views import _render_warranty
        await _render_warranty(q.message.chat_id, ctx, 0, edit=True, q=q)
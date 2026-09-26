# warranty.py
# Buyer claim + admin approve/reject.

from datetime import datetime, timedelta, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS, WARRANTY_DAYS
from db import get_db, sql as qs
from inventory import reserve_one
from ui_user import deliver_credentials, kb_main


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s) -> datetime:
    return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


async def open_claim_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = int(q.data.split(":", 1)[1])
    user_id = q.from_user.id

    async with get_db() as db:
        cur = await db.execute(qs("""
            SELECT o.*, a.email, a.expire_date
            FROM orders o JOIN accounts a ON a.id = o.account_id
            WHERE o.id=? AND o.user_id=?
        """), (order_id, user_id))
        row = await cur.fetchone()
    if not row:
        await q.edit_message_text("Order not found.", reply_markup=kb_main())
        return
    order = dict(row)

    if order["status"] != "delivered":
        await q.edit_message_text(
            f"⚠️ Warranty only on delivered orders. This one is {order['status']}.",
            reply_markup=kb_main(),
        )
        return

    delivered_at = _parse_dt(order["delivered_at"] or order["created_at"])
    age = _now() - delivered_at
    if age > timedelta(days=WARRANTY_DAYS):
        await q.edit_message_text(
            f"⌛ Warranty window ({WARRANTY_DAYS}d) expired.\n"
            f"Delivered {age.days}d ago. Contact @RBX404.",
            reply_markup=kb_main(),
        )
        return

    async with get_db() as db:
        cur = await db.execute(qs(
            "SELECT status FROM warranty WHERE order_id=? AND user_id=? ORDER BY id DESC LIMIT 1"
        ), (order_id, user_id))
        prev = await cur.fetchone()
    if prev and prev[0] in ("pending", "approved"):
        await q.edit_message_text(
            f"🧾 Claim already {prev[0]} for order #{order_id}.",
            reply_markup=kb_main(),
        )
        return

    ctx.user_data["warranty_order"] = order_id
    ctx.user_data["awaiting_warranty_reason"] = True
    await q.edit_message_text(
        f"⚠️ <b>Warranty claim — Order #{order_id}</b>\n\n"
        f"📧 {order['email']}\n📅 Expires: {order['expire_date']}\n"
        f"⏳ Delivered {age.days}d ago (window {WARRANTY_DAYS}d)\n\n"
        f"Describe the issue in one message.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_warranty")
        ]]),
    )


async def handle_warranty_reason(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.pop("awaiting_warranty_reason", False):
        return False
    order_id = ctx.user_data.pop("warranty_order", None)
    if not order_id:
        return False
    reason = (update.message.text or "").strip()
    if len(reason) < 5:
        await update.message.reply_text("Too short — describe the actual failure.")
        return True

    user_id = update.effective_user.id
    async with get_db() as db:
        cur = await db.execute(qs(
            "INSERT INTO warranty(user_id, order_id, reason) VALUES (?,?,?)"
        ), (user_id, order_id, reason))
        await db.commit()
        claim_id = getattr(cur, "lastrowid", None)
        if claim_id is None:
            cur = await db.execute(qs("SELECT MAX(id) FROM warranty"))
            claim_id = (await cur.fetchone())[0]

    await update.message.reply_text(
        f"🧾 Claim #{claim_id} received. Admin will review within 24h.",
        reply_markup=kb_main(),
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"wappr:{claim_id}"),
        InlineKeyboardButton("❌ Reject",  callback_data=f"wrej:{claim_id}"),
    ]])
    for admin in ADMIN_IDS:
        try:
            await ctx.bot.send_message(
                admin,
                f"⚠️ <b>Warranty claim #{claim_id}</b>\nOrder #{order_id}\n"
                f"User: <code>{user_id}</code>\n\n<i>{reason[:400]}</i>",
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception:
            pass
    return True


async def cancel_warranty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data.pop("awaiting_warranty_reason", None)
    ctx.user_data.pop("warranty_order", None)
    await q.edit_message_text("Cancelled.", reply_markup=kb_main())


async def admin_warranty_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    claim_id = int(q.data.split(":", 1)[1])

    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM warranty WHERE id=?"), (claim_id,))
        row = await cur.fetchone()
        if not row:
            await q.edit_message_text("Claim not found."); return
        claim = dict(row)
        if claim["status"] != "pending":
            await q.edit_message_text(f"Claim already {claim['status']}."); return
        cur = await db.execute(qs("SELECT * FROM orders WHERE id=?"), (claim["order_id"],))
        order = dict(await cur.fetchone())
        plan = order["plan"]

    replacement = await reserve_one(plan)
    if not replacement:
        await q.edit_message_text(f"⚠️ No stock for {plan}. Import more.")
        return

    async with get_db() as db:
        await db.execute(qs("UPDATE warranty SET status='approved' WHERE id=?"), (claim_id,))
        await db.execute(qs("UPDATE orders SET status='refunded' WHERE id=?"),
                         (claim["order_id"],))
        await db.execute(qs("UPDATE accounts SET status='DEAD' WHERE id=?"),
                         (order["account_id"],))
        cur = await db.execute(qs("""
            INSERT INTO orders(user_id, account_id, plan, payment, amount, status,
                               txn_ref, delivered_at)
            VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """), (claim["user_id"], replacement["id"], plan, "warranty", 0,
               "delivered", f"warranty_claim_{claim_id}"))
        await db.commit()
        new_order_id = getattr(cur, "lastrowid", None)
        if new_order_id is None:
            cur = await db.execute(qs("SELECT MAX(id) FROM orders"))
            new_order_id = (await cur.fetchone())[0]
        await db.execute(qs(
            "UPDATE accounts SET sold_to=?, sold_at=CURRENT_TIMESTAMP WHERE id=?"
        ), (claim["user_id"], replacement["id"]))
        await db.commit()

    await deliver_credentials(ctx, claim["user_id"], replacement, "warranty", 0,
                              order_id=new_order_id)
    await q.edit_message_text(f"✅ Claim #{claim_id} approved. Replacement delivered.")


async def admin_warranty_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    claim_id = int(q.data.split(":", 1)[1])

    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM warranty WHERE id=?"), (claim_id,))
        row = await cur.fetchone()
        if not row:
            await q.edit_message_text("Claim not found."); return
        claim = dict(row)
        if claim["status"] != "pending":
            await q.edit_message_text(f"Claim already {claim['status']}."); return
        await db.execute(qs("UPDATE warranty SET status='rejected' WHERE id=?"), (claim_id,))
        await db.commit()
    try:
        await ctx.bot.send_message(claim["user_id"],
                                   f"❌ Warranty claim #{claim_id} rejected.")
    except Exception:
        pass
    await q.edit_message_text(f"❌ Claim #{claim_id} rejected.")
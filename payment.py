# payment.py
# Manual cash + Stars invoice + admin approve/reject.

from telegram import (
    Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import PRICING, BKASH_NUMBER, NAGAD_NUMBER, ADMIN_IDS
from db import get_db, sql as qs
from ui_user import deliver_credentials, kb_main


async def send_stars_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             plan: str, account: dict):
    p = PRICING[plan]
    await ctx.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"RBX404 VPN · {p['label']}",
        description=f"ExpressVPN account · {plan} · expires {account['expire_date']}",
        payload=f"vpn:{plan}:{account['id']}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=p["label"], amount=p["stars"])],
    )


async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    data = q.invoice_payload or ""
    if not data.startswith("vpn:"):
        await q.answer(ok=False, error_message="Invalid payload.")
        return
    _, plan, acc_id = data.split(":")
    async with get_db() as db:
        cur = await db.execute(qs("SELECT status, sold_to FROM accounts WHERE id=?"),
                               (int(acc_id),))
        row = await cur.fetchone()
    if not row or row[0] != "SOLD" or row[1] is not None:
        await q.answer(ok=False, error_message="Account no longer available.")
        return
    await q.answer(ok=True)


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    _, plan, acc_id = (sp.invoice_payload or "").split(":")
    acc_id = int(acc_id)
    user_id = update.effective_user.id

    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM accounts WHERE id=?"), (acc_id,))
        row = await cur.fetchone()
        acct = dict(row)
        await db.execute(qs(
            "UPDATE accounts SET sold_to=?, sold_at=CURRENT_TIMESTAMP WHERE id=?"
        ), (user_id, acc_id))
        await db.execute(qs("""
            INSERT INTO orders(user_id, account_id, plan, payment, amount, status,
                               txn_ref, delivered_at)
            VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """), (user_id, acc_id, plan, "stars", sp.total_amount,
               "delivered", sp.telegram_payment_charge_id))
        await db.commit()

    await deliver_credentials(ctx, user_id, acct, "stars", sp.total_amount,
                              order_id=acc_id)


async def manual_cash_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, rail: str):
    plan = ctx.user_data.get("plan")
    acc_id = ctx.user_data.get("reserved")
    if not (plan and acc_id):
        await update.callback_query.edit_message_text("Session expired. /start")
        return
    p = PRICING[plan]
    number = BKASH_NUMBER if rail == "bkash" else NAGAD_NUMBER
    ctx.user_data["rail"] = rail
    text = (
        f"💳 <b>{rail.upper()} payment</b>\n\n"
        f"Send <b>{p['bdt']}৳</b> to:\n<code>{number}</code>\n"
        f"Reference: <code>RBX404-{acc_id}</code>\n\n"
        f"After sending, reply with your <b>Transaction ID</b>."
    )
    await update.callback_query.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="cancel")
        ]]),
    )
    ctx.user_data["awaiting_trxid"] = True


async def handle_trxid(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.pop("awaiting_trxid", False):
        return False
    trx = update.message.text.strip()
    plan = ctx.user_data.get("plan")
    acc_id = ctx.user_data.get("reserved")
    rail = ctx.user_data.get("rail")
    if not (plan and acc_id and rail):
        await update.message.reply_text("Session expired. /start")
        return True
    p = PRICING[plan]
    async with get_db() as db:
        cur = await db.execute(qs("""
            INSERT INTO orders(user_id, account_id, plan, payment, amount, status, txn_ref)
            VALUES (?,?,?,?,?,?,?)
        """), (update.effective_user.id, acc_id, plan, rail, p["bdt"], "pending", trx))
        await db.commit()
        order_id = getattr(cur, "lastrowid", None)
        if order_id is None:
            cur = await db.execute(qs("SELECT MAX(id) FROM orders"))
            row = await cur.fetchone()
            order_id = row[0]
    await update.message.reply_text(
        f"🧾 Order #{order_id} received.\nAwaiting admin verification."
    )
    for admin in ADMIN_IDS:
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"appr:{order_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"rej:{order_id}"),
        ]])
        try:
            await ctx.bot.send_message(
                admin,
                f"🧾 <b>New {rail.upper()} order</b>\nOrder #{order_id}\n"
                f"User: <code>{update.effective_user.id}</code>\nPlan: {plan}\n"
                f"Amount: {p['bdt']}৳\nTrxID: <code>{trx}</code>\nAccount: #{acc_id}",
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception:
            pass
    return True


async def admin_approve(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = int(q.data.split(":", 1)[1])
    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM orders WHERE id=?"), (order_id,))
        order = dict(await cur.fetchone())
        if order["status"] != "pending":
            await q.edit_message_text(f"Already {order['status']}.")
            return
        cur = await db.execute(qs("SELECT * FROM accounts WHERE id=?"),
                               (order["account_id"],))
        acct = dict(await cur.fetchone())
        await db.execute(qs(
            "UPDATE orders SET status='delivered', delivered_at=CURRENT_TIMESTAMP WHERE id=?"
        ), (order_id,))
        await db.execute(qs(
            "UPDATE accounts SET sold_to=?, sold_at=CURRENT_TIMESTAMP WHERE id=?"
        ), (order["user_id"], order["account_id"]))
        await db.commit()
    await deliver_credentials(ctx, order["user_id"], acct, order["payment"],
                              order["amount"], order_id=order_id)
    await q.edit_message_text(f"✅ Order #{order_id} delivered.")


async def admin_reject(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    order_id = int(q.data.split(":", 1)[1])
    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM orders WHERE id=?"), (order_id,))
        order = dict(await cur.fetchone())
        await db.execute(qs("UPDATE orders SET status='cancelled' WHERE id=?"), (order_id,))
        await db.commit()
    from inventory import release_reserved
    await release_reserved(order["account_id"])
    try:
        await ctx.bot.send_message(order["user_id"],
                                   f"❌ Order #{order_id} rejected. Contact @RBX404.")
    except Exception:
        pass
    await q.edit_message_text(f"❌ Order #{order_id} rejected.")
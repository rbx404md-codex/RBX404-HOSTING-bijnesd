# payment.py
# Manual cash + Stars invoice + admin approve/reject.

from telegram import (
    Update, LabeledPrice, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import PRICING, BKASH_NUMBER, NAGAD_NUMBER, ADMIN_IDS
from db import get_db, sql as qs, insert_returning_id
from inventory import finalize_sale
from promos import record_redemption
from ui_user import deliver_credentials, kb_main, current_price


async def send_stars_invoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                             plan: str, account: dict):
    p = PRICING[plan]
    bdt, stars = current_price(ctx, plan)
    promo_code = ctx.user_data.get("promo_code")
    if promo_code:
        # The invoice price is fixed the moment Telegram shows it to the
        # user, so the promo is committed here rather than at
        # successful_payment — see promos.record_redemption docstring.
        await record_redemption(promo_code, update.effective_user.id, p["bdt"], bdt)
        ctx.user_data.pop("promo_code", None)
        ctx.user_data.pop("promo_bdt", None)
        ctx.user_data.pop("promo_stars", None)
    await ctx.bot.send_invoice(
        chat_id=update.effective_chat.id,
        title=f"RBX404 VPN · {p['label']}",
        description=f"ExpressVPN account · {plan} · expires {account['expire_date']}",
        payload=f"vpn:{plan}:{account['id']}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=p["label"], amount=stars)],
    )


async def pre_checkout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.pre_checkout_query
    data = q.invoice_payload or ""
    if not data.startswith("vpn:"):
        await q.answer(ok=False, error_message="Invalid payload.")
        return
    _, plan, acc_id = data.split(":")
    async with get_db() as db:
        cur = await db.execute(qs("SELECT status FROM accounts WHERE id=?"),
                               (int(acc_id),))
        row = await cur.fetchone()
    if not row or row["status"] != "RESERVED":
        await q.answer(ok=False, error_message="Reservation expired — please choose the plan again.")
        return
    await q.answer(ok=True)


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    _, plan, acc_id = (sp.invoice_payload or "").split(":")
    acc_id = int(acc_id)
    user_id = update.effective_user.id
    charge_id = sp.telegram_payment_charge_id

    async with get_db() as db:
        # Telegram can redeliver the same successful_payment update (e.g.
        # on a retried webhook or client reconnect). Without this check
        # the same charge would create a second order and re-deliver the
        # account's credentials to the user. txn_ref carries Telegram's
        # own charge id, which is unique per payment, so use it as the
        # idempotency key.
        cur = await db.execute(
            qs("SELECT id FROM orders WHERE payment='stars' AND txn_ref=?"),
            (charge_id,),
        )
        if await cur.fetchone():
            return

        cur = await db.execute(qs("SELECT * FROM accounts WHERE id=?"), (acc_id,))
        row = await cur.fetchone()
        acct = dict(row)

    # The user already paid at this point (Telegram Stars charge is final),
    # so finalize_sale failing here means the reservation lapsed under the
    # user mid-payment — extremely rare given pre_checkout just verified
    # it, but if it happens we must not silently lose the payment: flag
    # admins for a manual refund/resolution instead of delivering nothing
    # or double-delivering another account.
    if not await finalize_sale(acc_id, user_id):
        async with get_db() as db:
            await db.execute(qs("""
                INSERT INTO orders(user_id, account_id, plan, payment, amount, status, txn_ref)
                VALUES (?,?,?,?,?,?,?)
            """), (user_id, acc_id, plan, "stars", sp.total_amount, "needs_refund", charge_id))
            await db.commit()
        for admin in ADMIN_IDS:
            try:
                await ctx.bot.send_message(
                    admin,
                    f"🚨 Stars payment {sp.total_amount}⭐ from <code>{user_id}</code> "
                    f"for account #{acc_id} arrived after its reservation lapsed — "
                    f"needs manual refund/replacement.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception:
                pass
        await update.message.reply_text(
            "⚠️ That reservation just expired as your payment came in. "
            "Support has been notified and will resolve this manually."
        )
        return

    async with get_db() as db:
        await db.execute(qs("""
            INSERT INTO orders(user_id, account_id, plan, payment, amount, status,
                               txn_ref, delivered_at)
            VALUES (?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """), (user_id, acc_id, plan, "stars", sp.total_amount,
               "delivered", charge_id))
        await db.commit()

    await deliver_credentials(ctx, user_id, acct, "stars", sp.total_amount,
                              order_id=acc_id)


async def manual_cash_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, rail: str):
    plan = ctx.user_data.get("plan")
    acc_id = ctx.user_data.get("reserved")
    if not (plan and acc_id):
        await update.callback_query.edit_message_text("Session expired. /start")
        return
    bdt, _ = current_price(ctx, plan)
    number = BKASH_NUMBER if rail == "bkash" else NAGAD_NUMBER
    ctx.user_data["rail"] = rail
    text = (
        f"💳 <b>{rail.upper()} payment</b>\n\n"
        f"Send <b>{bdt}৳</b> to:\n<code>{number}</code>\n"
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
    bdt, _ = current_price(ctx, plan)
    promo_code = ctx.user_data.pop("promo_code", None)
    ctx.user_data.pop("promo_bdt", None)
    ctx.user_data.pop("promo_stars", None)
    async with get_db() as db:
        order_id = await insert_returning_id(db, """
            INSERT INTO orders(user_id, account_id, plan, payment, amount, status, txn_ref)
            VALUES (?,?,?,?,?,?,?)
        """, (update.effective_user.id, acc_id, plan, rail, bdt, "pending", trx))
        await db.commit()
    if promo_code:
        await record_redemption(promo_code, update.effective_user.id, p["bdt"], bdt,
                                order_id=order_id)
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
                f"Amount: {bdt}৳\nTrxID: <code>{trx}</code>\nAccount: #{acc_id}",
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
        cur = await db.execute(qs(
            "UPDATE orders SET status='delivered', delivered_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status='pending'"
        ), (order_id,))
        if cur.rowcount != 1:
            # Another admin approved/rejected this order in the meantime.
            await db.commit()
            await q.edit_message_text("Already handled by another admin.")
            return
        await db.commit()
        cur = await db.execute(qs("SELECT * FROM accounts WHERE id=?"),
                               (order["account_id"],))
        acct = dict(await cur.fetchone())

    if not await finalize_sale(order["account_id"], order["user_id"]):
        await q.edit_message_text(
            f"⚠️ Order #{order_id}: account reservation lapsed before approval — "
            f"reserve a replacement manually."
        )
        return

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
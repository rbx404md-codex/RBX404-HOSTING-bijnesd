# main_handlers.py
# Single source of handler registration.

import logging
from datetime import time as dtime
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    CommandHandler, CallbackQueryHandler, MessageHandler,
    PreCheckoutQueryHandler, filters,
)

from ui_user import cmd_start, cb_router
from payment import (
    pre_checkout, successful_payment, manual_cash_prompt,
    admin_approve, admin_reject, send_stars_invoice, handle_trxid,
)
from admin import (
    cmd_admin, cmd_import, cmd_broadcast, cmd_promo, cmd_runjob, cb_admin,
    collect_import,
)
from inventory import stock_by_plan
from warranty import (
    open_claim_prompt, cancel_warranty, handle_warranty_reason,
    admin_warranty_approve, admin_warranty_reject,
)
from tickets import (
    cmd_ticket, cancel_ticket, list_user_tickets,
    admin_ticket_reply_prompt, admin_ticket_close, handle_ticket_body,
    handle_ticket_reply,
)
from admin_views import (
    cmd_orders, cmd_tickets_list, cmd_warranty_list,
    cb_orders, cb_tickets, cb_warranty, cb_noop,
)
from db import get_db, sql as qs
from config import DB_PATH, USE_POSTGRES

log = logging.getLogger("rbx404.errors")


async def cmd_stock(update: Update, ctx):
    s = await stock_by_plan()
    await update.message.reply_text(
        "📦 Stock: " + " · ".join(f"{k}:{v}" for k, v in s.items())
    )


async def on_text(update: Update, ctx):
    if await collect_import(update, ctx):         return
    if await handle_trxid(update, ctx):           return
    if await handle_warranty_reason(update, ctx): return
    if await handle_ticket_body(update, ctx):     return
    if await handle_ticket_reply(update, ctx):    return
    await update.message.reply_text("Use /start.")


async def cb_payment_branch(update: Update, ctx):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data.startswith("pay:"):
        rail = data.split(":", 1)[1]
        if rail == "stars":
            plan = ctx.user_data.get("plan")
            acc_id = ctx.user_data.get("reserved")
            if not (plan and acc_id):
                await q.edit_message_text("Session expired. /start")
                return
            async with get_db() as db:
                cur = await db.execute(qs("SELECT * FROM accounts WHERE id=?"), (acc_id,))
                row = await cur.fetchone()
                acct = dict(row)
            await send_stars_invoice(update, ctx, plan, acct)
        else:
            await manual_cash_prompt(update, ctx, rail)
        return
    if data.startswith("appr:"):
        await admin_approve(update, ctx); return
    if data.startswith("rej:"):
        await admin_reject(update, ctx); return


async def on_error(update: object, ctx) -> None:
    """Global error handler — logs and tells the user (if we can)."""
    log.exception("handler error: %s", ctx.error)
    if isinstance(update, Update):
        try:
            if update.effective_message:
                await update.effective_message.reply_text(
                    "⚠️ Something went wrong. Try /start again."
                )
        except TelegramError:
            pass


def register_handlers(app):
    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("import", cmd_import))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("promo", cmd_promo))
    app.add_handler(CommandHandler("stock", cmd_stock))
    app.add_handler(CommandHandler("ticket", cmd_ticket))
    app.add_handler(CommandHandler("mytickets", list_user_tickets))
    app.add_handler(CommandHandler("tickets", cmd_tickets_list))
    app.add_handler(CommandHandler("orders", cmd_orders))
    app.add_handler(CommandHandler("warranty", cmd_warranty_list))
    app.add_handler(CommandHandler("runjob", cmd_runjob))

    app.add_handler(PreCheckoutQueryHandler(pre_checkout))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment))

    # callbacks — ORDER: admin → lists → payment → warranty → ticket → user
    app.add_handler(CallbackQueryHandler(cb_admin,                pattern=r"^a:"))
    app.add_handler(CallbackQueryHandler(cb_orders,               pattern=r"^ao:"))
    app.add_handler(CallbackQueryHandler(cb_tickets,              pattern=r"^at:"))
    app.add_handler(CallbackQueryHandler(cb_warranty,             pattern=r"^aw:"))
    app.add_handler(CallbackQueryHandler(cb_noop,                 pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(cb_payment_branch,       pattern=r"^(pay|appr|rej):"))
    app.add_handler(CallbackQueryHandler(open_claim_prompt,       pattern=r"^warr:"))
    app.add_handler(CallbackQueryHandler(cancel_warranty,         pattern=r"^cancel_warranty$"))
    app.add_handler(CallbackQueryHandler(admin_warranty_approve,  pattern=r"^wappr:"))
    app.add_handler(CallbackQueryHandler(admin_warranty_reject,   pattern=r"^wrej:"))
    app.add_handler(CallbackQueryHandler(cancel_ticket,           pattern=r"^cancel_ticket$"))
    app.add_handler(CallbackQueryHandler(admin_ticket_reply_prompt, pattern=r"^tkreply:"))
    app.add_handler(CallbackQueryHandler(admin_ticket_close,      pattern=r"^tkclose:"))
    app.add_handler(CallbackQueryHandler(cb_router))  # catch-all, MUST be last

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # errors
    app.add_error_handler(on_error)

    schedule_jobs(app)


def schedule_jobs(app):
    jq = getattr(app, "job_queue", None)
    if jq is None:
        return
    from jobs import (
        job_release_referrals, job_low_stock, job_daily_digest,
        job_expiry_sweep, job_backup,
    )
    jq.run_repeating(job_release_referrals, interval=3600, first=60)
    jq.run_repeating(job_low_stock, interval=1800, first=120)
    jq.run_repeating(job_expiry_sweep, interval=21600, first=300)
    jq.run_repeating(job_backup, interval=21600, first=600)
    jq.run_daily(job_daily_digest, time=dtime(hour=17, minute=59))

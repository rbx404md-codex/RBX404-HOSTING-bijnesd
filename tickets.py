# tickets.py
# Support ticketing.

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from db import get_db, sql as qs, insert_returning_id
from ui_user import kb_main


def kb_ticket_actions(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Reply", callback_data=f"tkreply:{ticket_id}")],
        [InlineKeyboardButton("✅ Close", callback_data=f"tkclose:{ticket_id}")],
    ])


async def cmd_ticket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /ticket <short subject>")
        return
    subject = " ".join(ctx.args)[:120]
    ctx.user_data["ticket_subject"] = subject
    ctx.user_data["awaiting_ticket_body"] = True
    await update.message.reply_text(
        f"📮 Subject: <b>{subject}</b>\n\nSend the body in one message.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_ticket")
        ]]),
    )


async def handle_ticket_body(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.pop("awaiting_ticket_body", False):
        return False
    subject = ctx.user_data.pop("ticket_subject", "(no subject)")
    body = (update.message.text or "").strip()
    if not body:
        return True
    user_id = update.effective_user.id

    async with get_db() as db:
        ticket_id = await insert_returning_id(
            db, "INSERT INTO tickets(user_id, subject, body) VALUES (?,?,?)",
            (user_id, subject, body),
        )
        await db.commit()

    await update.message.reply_text(
        f"📮 Ticket #{ticket_id} opened.", reply_markup=kb_main(),
    )
    kb = kb_ticket_actions(ticket_id)
    for admin in ADMIN_IDS:
        try:
            await ctx.bot.send_message(
                admin,
                f"🆘 <b>Ticket #{ticket_id}</b>\nUser: <code>{user_id}</code>\n"
                f"Subject: {subject}\n\n<i>{body[:600]}</i>",
                parse_mode=ParseMode.HTML, reply_markup=kb,
            )
        except Exception:
            pass
    return True


async def cancel_ticket(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data.pop("awaiting_ticket_body", None)
    ctx.user_data.pop("ticket_subject", None)
    await q.edit_message_text("Cancelled.", reply_markup=kb_main())


async def list_user_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with get_db() as db:
        cur = await db.execute(qs("""
            SELECT id, subject, status, created_at FROM tickets
            WHERE user_id=? ORDER BY id DESC LIMIT 10
        """), (user_id,))
        rows = await cur.fetchall()
    if not rows:
        await update.message.reply_text("No tickets yet. /ticket <subject>")
        return
    txt = "📮 <b>Your tickets</b>\n\n" + "\n".join(
        f"#{r[0]} · {r[2]} · {str(r[1])[:40]} · {r[3]}" for r in rows
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.HTML)


async def admin_ticket_reply_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    ticket_id = int(q.data.split(":", 1)[1])
    ctx.user_data["ticket_reply_id"] = ticket_id
    ctx.user_data["awaiting_ticket_reply"] = True
    await q.edit_message_text(f"✍️ Send the reply body for ticket #{ticket_id}.")


async def handle_ticket_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    if not ctx.user_data.pop("awaiting_ticket_reply", False):
        return False
    if update.effective_user.id not in ADMIN_IDS:
        return True
    ticket_id = ctx.user_data.pop("ticket_reply_id", None)
    if not ticket_id:
        return True
    reply = (update.message.text or "").strip()
    if not reply:
        return True

    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM tickets WHERE id=?"), (ticket_id,))
        row = await cur.fetchone()
        if not row:
            await update.message.reply_text("Ticket not found."); return True
        tk = dict(row)
        await db.execute(qs("UPDATE tickets SET status='answered' WHERE id=?"), (ticket_id,))
        await db.commit()

    try:
        await ctx.bot.send_message(
            tk["user_id"],
            f"📮 <b>Reply — Ticket #{ticket_id}</b>\n\n<i>{reply}</i>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await update.message.reply_text(f"⚠️ Could not deliver: {e}")
    await update.message.reply_text(f"✅ Reply sent for ticket #{ticket_id}.")
    return True


async def admin_ticket_close(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    ticket_id = int(q.data.split(":", 1)[1])
    async with get_db() as db:
        cur = await db.execute(qs("SELECT user_id FROM tickets WHERE id=?"), (ticket_id,))
        row = await cur.fetchone()
        await db.execute(qs("UPDATE tickets SET status='closed' WHERE id=?"), (ticket_id,))
        await db.commit()
    if row:
        try:
            await ctx.bot.send_message(row[0], f"✅ Ticket #{ticket_id} closed.")
        except Exception:
            pass
    await q.edit_message_text(f"✅ Ticket #{ticket_id} closed.")
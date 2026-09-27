# admin_views.py
# Paginated list views for orders / tickets / warranty.

from math import ceil
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from db import get_db, sql as qs

PAGE = 8


def _pager(prefix: str, page: int, total_pages: int):
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️", callback_data=f"{prefix}:{page-1}"))
    row.append(InlineKeyboardButton(f"{page+1}/{max(total_pages,1)}", callback_data="noop"))
    if page < total_pages - 1:
        row.append(InlineKeyboardButton("➡️", callback_data=f"{prefix}:{page+1}"))
    return [row]


async def cmd_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    page = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 0
    await _render_orders(update.effective_chat.id, ctx, page, edit=False)


async def cb_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    await _render_orders(q.message.chat_id, ctx, int(q.data.split(":",1)[1]), edit=True, q=q)


async def _render_orders(chat_id, ctx, page: int, edit: bool, q=None):
    async with get_db() as db:
        cur = await db.execute(qs("SELECT COUNT(*) FROM orders"))
        total = (await cur.fetchone())[0]
        tp = ceil(total / PAGE) if total else 1
        page = max(0, min(page, tp - 1))
        cur = await db.execute(qs("""
            SELECT o.id, o.user_id, o.plan, o.payment, o.amount, o.status,
                   o.created_at, a.email
            FROM orders o JOIN accounts a ON a.id = o.account_id
            ORDER BY o.id DESC LIMIT ? OFFSET ?
        """), (PAGE, page * PAGE))
        rows = [dict(r) for r in await cur.fetchall()]

    lines = [f"🧾 <b>Orders</b>  ({total} total · page {page+1}/{tp})\n"]
    kb_rows = []
    for r in rows:
        unit = "⭐" if r["payment"] == "stars" else "৳"
        lines.append(
            f"#{r['id']} · {r['status']} · {r['plan']} · {r['amount']}{unit}\n"
            f"  {r['email']} · u:{r['user_id']} · {r['payment']}"
        )
        if r["status"] == "pending":
            kb_rows.append([
                InlineKeyboardButton(f"✅ #{r['id']}", callback_data=f"appr:{r['id']}"),
                InlineKeyboardButton(f"❌ #{r['id']}", callback_data=f"rej:{r['id']}"),
            ])
    kb_rows += _pager("ao", page, tp)
    kb_rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="a:stats")])
    kb = InlineKeyboardMarkup(kb_rows)
    text = "\n".join(lines)
    if edit and q:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_tickets_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    page = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 0
    await _render_tickets(update.effective_chat.id, ctx, page, edit=False)


async def cb_tickets(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    await _render_tickets(q.message.chat_id, ctx, int(q.data.split(":",1)[1]), edit=True, q=q)


async def _render_tickets(chat_id, ctx, page: int, edit: bool, q=None):
    async with get_db() as db:
        cur = await db.execute(qs("SELECT COUNT(*) FROM tickets WHERE status!='closed'"))
        total = (await cur.fetchone())[0]
        tp = ceil(total / PAGE) if total else 1
        page = max(0, min(page, tp - 1))
        cur = await db.execute(qs("""
            SELECT id, user_id, subject, status, created_at FROM tickets
            WHERE status != 'closed' ORDER BY id DESC LIMIT ? OFFSET ?
        """), (PAGE, page * PAGE))
        rows = [dict(r) for r in await cur.fetchall()]

    lines = [f"🆘 <b>Open Tickets</b>  ({total} · page {page+1}/{tp})\n"]
    kb_rows = []
    for r in rows:
        lines.append(f"#{r['id']} · {r['status']} · u:{r['user_id']} · {str(r['subject'])[:50]}")
        kb_rows.append([
            InlineKeyboardButton(f"✍️ #{r['id']}", callback_data=f"tkreply:{r['id']}"),
            InlineKeyboardButton(f"✅ #{r['id']}", callback_data=f"tkclose:{r['id']}"),
        ])
    kb_rows += _pager("at", page, tp)
    kb_rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="a:stats")])
    kb = InlineKeyboardMarkup(kb_rows)
    text = "\n".join(lines)
    if edit and q:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cmd_warranty_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    page = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 0
    await _render_warranty(update.effective_chat.id, ctx, page, edit=False)


async def cb_warranty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("Admins only.", show_alert=True); return
    await q.answer()
    await _render_warranty(q.message.chat_id, ctx, int(q.data.split(":",1)[1]), edit=True, q=q)


async def _render_warranty(chat_id, ctx, page: int, edit: bool, q=None):
    async with get_db() as db:
        cur = await db.execute(qs("SELECT COUNT(*) FROM warranty WHERE status='pending'"))
        total = (await cur.fetchone())[0]
        tp = ceil(total / PAGE) if total else 1
        page = max(0, min(page, tp - 1))
        cur = await db.execute(qs("""
            SELECT id, user_id, order_id, reason, status, created_at FROM warranty
            WHERE status='pending' ORDER BY id DESC LIMIT ? OFFSET ?
        """), (PAGE, page * PAGE))
        rows = [dict(r) for r in await cur.fetchall()]

    lines = [f"⚠️ <b>Pending Warranty</b>  ({total} · page {page+1}/{tp})\n"]
    kb_rows = []
    for r in rows:
        lines.append(f"#{r['id']} · order#{r['order_id']} · u:{r['user_id']}\n"
                     f"  <i>{str(r['reason'] or '')[:80]}</i>")
        kb_rows.append([
            InlineKeyboardButton(f"✅ #{r['id']}", callback_data=f"wappr:{r['id']}"),
            InlineKeyboardButton(f"❌ #{r['id']}", callback_data=f"wrej:{r['id']}"),
        ])
    kb_rows += _pager("aw", page, tp)
    kb_rows.append([InlineKeyboardButton("⬅️ Panel", callback_data="a:stats")])
    kb = InlineKeyboardMarkup(kb_rows)
    text = "\n".join(lines)
    if edit and q:
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    else:
        await ctx.bot.send_message(chat_id, text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def cb_noop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
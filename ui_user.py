# ui_user.py
# User-facing UI: keyboards, callbacks, delivery.

from datetime import datetime, timezone, timedelta
from html import escape as esc
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import (
    PRICING, FORCE_JOIN_CHANNELS, WARRANTY_DAYS, ADMIN_IDS, REFERRAL_STARS,
)
from db import get_db, sql as qs
from inventory import reserve_one, release_reserved, generate_ovpn

PLAN_LABEL = {k: v["label"] for k, v in PRICING.items()}


def current_price(ctx: ContextTypes.DEFAULT_TYPE, plan: str) -> tuple[int, int]:
    """(bdt, stars) for this checkout — discounted if a valid promo is
    currently applied in ctx.user_data, otherwise the plan's list price."""
    if ctx.user_data.get("promo_bdt") is not None:
        return ctx.user_data["promo_bdt"], ctx.user_data["promo_stars"]
    p = PRICING[plan]
    return p["bdt"], p["stars"]


async def _send_price_preview(q, ctx: ContextTypes.DEFAULT_TYPE, acct: dict):
    plan = ctx.user_data["plan"]
    p = PRICING[plan]
    bdt, stars = current_price(ctx, plan)
    price_line = f"💰 Price: {bdt}৳ / {stars}⭐"
    if ctx.user_data.get("promo_code"):
        price_line += f" (🎟️ {ctx.user_data['promo_code']} applied, was {p['bdt']}৳/{p['stars']}⭐)"
    preview = (
        f"📧 Email: <code>{esc(acct['email'])}</code>\n"
        f"🌍 Country: {esc(acct['country'])}\n"
        f"📅 Plan: {p['label']}\n"
        f"⏳ Expires: {esc(acct['expire_date'])}\n"
        f"{price_line}\n\nChoose payment, or send /promo CODE for a discount:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 bKash", callback_data="pay:bkash"),
         InlineKeyboardButton("💳 Nagad", callback_data="pay:nagad")],
        [InlineKeyboardButton(f"⭐ Telegram Stars ({stars}⭐)", callback_data="pay:stars")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await q.edit_message_text(preview, parse_mode=ParseMode.HTML, reply_markup=kb)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 Buy VPN", callback_data="buy")],
        [InlineKeyboardButton("📦 My Accounts", callback_data="myacc"),
         InlineKeyboardButton("🎁 Refer", callback_data="ref")],
        [InlineKeyboardButton("📊 Stats", callback_data="stats"),
         InlineKeyboardButton("🆘 Support", callback_data="support")],
        [InlineKeyboardButton("🎟️ Promo", callback_data="promo"),
         InlineKeyboardButton("🌐 Language", callback_data="lang")],
    ])


def kb_plans() -> InlineKeyboardMarkup:
    rows = []
    for plan, p in PRICING.items():
        rows.append([InlineKeyboardButton(
            f"{p['label']} — {p['bdt']}৳ / {p['stars']}⭐",
            callback_data=f"plan:{plan}",
        )])
    rows.append([InlineKeyboardButton("⬅️ Back", callback_data="home")])
    return InlineKeyboardMarkup(rows)


async def _ensure_force_join(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    if user_id in ADMIN_IDS:
        return True
    missing = []
    for ch in FORCE_JOIN_CHANNELS:
        try:
            m = await ctx.bot.get_chat_member(ch["id"], user_id)
            if m.status in ("left", "kicked"):
                missing.append(ch)
        except Exception:
            missing.append(ch)
    if not missing:
        return True
    rows = [[InlineKeyboardButton(f"Join {c['id']}", url=c["url"])] for c in missing]
    rows.append([InlineKeyboardButton("✅ I joined", callback_data="checkjoin")])
    await update.effective_message.reply_text(
        "🔒 Please join our channels to use the bot:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return False


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _ensure_force_join(update, ctx):
        return
    u = update.effective_user

    # referral deep-link handling
    if ctx.args and ctx.args[0].startswith("ref_"):
        try:
            referrer = int(ctx.args[0][4:])
        except ValueError:
            referrer = None
        if referrer and referrer != u.id:
            async with get_db() as db:
                cur = await db.execute(qs("SELECT 1 FROM users WHERE user_id=?"), (u.id,))
                exists = await cur.fetchone()
                if not exists:
                    now = datetime.now(timezone.utc)
                    eligible = (now + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        await db.execute(qs("""
                            INSERT INTO referrals(referrer_id, referee_id, eligible_at)
                            VALUES (?,?,?)
                        """), (referrer, u.id, eligible))
                        await db.commit()
                    except Exception:
                        pass

    async with get_db() as db:
        await db.execute(qs("""
            INSERT INTO users(user_id, username, first_name, last_seen)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET last_seen=CURRENT_TIMESTAMP,
                username=excluded.username, first_name=excluded.first_name
        """), (u.id, u.username or "", u.first_name or ""))
        await db.commit()

    safe_name = esc(u.first_name or "there")
    await update.message.reply_text(
        f"🔒 <b>RBX404 VPN Premium Store</b>\n\nWelcome, {safe_name}!",
        parse_mode=ParseMode.HTML, reply_markup=kb_main(),
    )


async def cb_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "home":
        await q.edit_message_text("🔒 <b>RBX404 VPN Premium Store</b>",
                                  parse_mode=ParseMode.HTML, reply_markup=kb_main())
        return

    if data == "buy":
        await q.edit_message_text("Select a plan:", reply_markup=kb_plans())
        return

    if data.startswith("plan:"):
        plan = data.split(":", 1)[1]
        ctx.user_data["plan"] = plan
        # A promo picked for a previous plan may not be valid for this one
        # (plan-restricted codes) — don't carry it over silently.
        ctx.user_data.pop("promo_code", None)
        ctx.user_data.pop("promo_bdt", None)
        ctx.user_data.pop("promo_stars", None)
        acct = await reserve_one(plan, user_id=update.effective_user.id)
        if not acct:
            await q.edit_message_text(
                f"❌ No stock for {PLAN_LABEL.get(plan, plan)}.",
                reply_markup=kb_plans(),
            )
            return
        ctx.user_data["reserved"] = acct["id"]
        await _send_price_preview(q, ctx, acct)
        return

    if data == "cancel":
        rid = ctx.user_data.pop("reserved", None)
        if rid:
            await release_reserved(rid)
        await q.edit_message_text("Cancelled.", reply_markup=kb_main())
        return

    if data == "myacc":
        async with get_db() as db:
            cur = await db.execute(qs("""
                SELECT o.id, a.email, a.plan, a.expire_date, o.status, o.delivered_at
                FROM orders o JOIN accounts a ON a.id = o.account_id
                WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20
            """), (q.from_user.id,))
            rows = [dict(r) for r in await cur.fetchall()]
        if not rows:
            await q.edit_message_text("No purchases yet.", reply_markup=kb_main())
            return
        await q.edit_message_text("📦 <b>Your purchases</b>", parse_mode=ParseMode.HTML)
        now = datetime.now(timezone.utc)
        for r in rows:
            line = (
                f"#{r['id']} · {esc(r['email'])}\n"
                f"Plan: {r['plan']} · Expires: {esc(str(r['expire_date']))}\n"
                f"Status: {r['status']}"
            )
            kb_rows = []
            if r["status"] == "delivered" and r["delivered_at"]:
                try:
                    dt = datetime.strptime(str(r["delivered_at"])[:19], "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc)
                    if now - dt <= timedelta(days=WARRANTY_DAYS):
                        kb_rows.append([InlineKeyboardButton(
                            "⚠️ Warranty claim", callback_data=f"warr:{r['id']}"
                        )])
                except Exception:
                    pass
            await q.message.reply_text(
                line, parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(kb_rows) if kb_rows else None,
            )
        return

    if data == "ref":
        async with get_db() as db:
            cur = await db.execute(qs("SELECT ref_count FROM users WHERE user_id=?"),
                                   (q.from_user.id,))
            r = await cur.fetchone()
        n = (r[0] if r else 0) or 0
        link = f"https://t.me/{ctx.bot.username}?start=ref_{q.from_user.id}"
        await q.edit_message_text(
            f"🎁 <b>Referral</b>\n"
            f"Invite friends — earn <b>{REFERRAL_STARS}⭐</b> per verified join.\n"
            f"Bonus releases after invitee stays in channel 7 days.\n\n"
            f"Your link: <code>{esc(link)}</code>\nConfirmed referrals: <b>{n}</b>",
            parse_mode=ParseMode.HTML, reply_markup=kb_main(),
        )
        return

    if data == "support":
        await q.edit_message_text(
            "🆘 Contact admin: @RBX404\nOpen a ticket: /ticket <subject>",
            reply_markup=kb_main(),
        )
        return

    if data == "promo":
        await q.edit_message_text("🎟️ Send promo code: /promo CODE",
                                  reply_markup=kb_main())
        return

    if data == "lang":
        await q.edit_message_text("🌐 /lang bn | en", reply_markup=kb_main())
        return

    if data == "stats":
        async with get_db() as db:
            cur = await db.execute(qs(
                "SELECT COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE user_id=? AND status='delivered'"
            ), (q.from_user.id,))
            n, spent = await cur.fetchone()
        await q.edit_message_text(f"📊 Orders: {n}\n💰 Spent: {spent}",
                                  reply_markup=kb_main())
        return

    if data == "checkjoin":
        if await _ensure_force_join(update, ctx):
            await q.edit_message_text("✅ Verified. /start again.", reply_markup=kb_main())
        return


async def deliver_credentials(ctx, user_id: int, account: dict,
                              payment: str, amount: int, order_id: int):
    from config import LOG_CHANNEL_ID
    p = PRICING[account["plan"]]
    ovpn_path = generate_ovpn(account)
    msg = (
        f"✅ <b>Account Delivered!</b>\n\n"
        f"📧 Email: <code>{esc(account['email'])}</code>\n"
        f"🔑 Password: <code>{esc(account['password'])}</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🔒 <b>VPN Credentials:</b>\n"
        f"👤 OVPN User: <code>{esc(account['ovpn_user'])}</code>\n"
        f"🔐 OVPN Pass: <code>{esc(account['ovpn_pass'])}</code>\n\n"
        f"📅 Expires: {esc(str(account['expire_date']))}\n"
        f"⏳ Days Left: {account['days_left']}\n"
        f"📋 Plan: {p['label']}\n"
        f"⚡ Status: {esc(account['status'])}\n"
        f"🔑 License: <code>{esc(account.get('license') or '-')}</code>\n"
        f"📡 PPTP: <code>{esc(account.get('pptp') or '-')}</code>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"📱 <b>Setup:</b>\n"
        f"1. Install OpenVPN Connect\n"
        f"2. Import the attached .ovpn\n"
        f"3. Enter username/password above\n"
        f"4. Connect ✅\n\n"
        f"⚠️ Warranty: {WARRANTY_DAYS} days replacement\n"
        f"📞 Support: @RBX404\n\n"
        f"🧾 <i>Order #{order_id} · paid via {esc(payment)} · no refund on digital goods</i>"
    )
    with open(ovpn_path, "rb") as f:
        await ctx.bot.send_document(
            chat_id=user_id, document=f, filename=f"RBX404_{account['id']}.ovpn",
            caption=msg, parse_mode=ParseMode.HTML,
        )
    if LOG_CHANNEL_ID:
        try:
            with open(ovpn_path, "rb") as f:
                await ctx.bot.send_document(
                    chat_id=LOG_CHANNEL_ID, document=f,
                    caption=f"DELIVERED order #{order_id} to <code>{user_id}</code>",
                    parse_mode=ParseMode.HTML,
                )
        except Exception:
            pass

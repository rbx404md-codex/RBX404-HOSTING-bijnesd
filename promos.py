# promos.py
# Promo code validation, discounted pricing, and redemption tracking.
#
# Before this module: /promo was admin-only (so the "Send promo code:
# /promo CODE" prompt shown to regular users just replied "Admins only."),
# the promos.used counter was never incremented, and no price anywhere
# ever applied a discount. This wires the whole thing together.

from datetime import datetime, timezone
from db import get_db, sql as qs

MIN_PCT = 1
MAX_PCT = 90  # never allow a promo to discount more than this


def validate_admin_input(pct: int, max_uses: int, min_order_bdt: int, per_user_limit: int) -> str | None:
    """Returns an error string, or None if all values are in safe ranges."""
    if not (MIN_PCT <= pct <= MAX_PCT):
        return f"Percent must be between {MIN_PCT} and {MAX_PCT}."
    if max_uses < 0:
        return "Max uses can't be negative."
    if min_order_bdt < 0:
        return "Minimum order amount can't be negative."
    if per_user_limit < 1:
        return "Per-user limit must be at least 1."
    return None


async def validate_and_price(code: str, user_id: int, plan: str, base_bdt: int, base_stars: int) -> dict:
    """Check a promo code against every rule and return discounted prices.

    Returns {"ok": True, "bdt": int, "stars": int, "pct": int} on success,
    or {"ok": False, "error": str} on any failure — never silently
    discounts if a rule can't be verified.
    """
    code = code.strip().upper()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with get_db() as db:
        cur = await db.execute(qs("SELECT * FROM promos WHERE code=?"), (code,))
        row = await cur.fetchone()
        if not row:
            return {"ok": False, "error": "Promo code not found."}
        promo = dict(row)

        if not promo.get("active", 1):
            return {"ok": False, "error": "This promo is no longer active."}
        if promo.get("expires_at") and str(promo["expires_at"]) < now:
            return {"ok": False, "error": "This promo has expired."}

        max_uses = promo.get("max_uses") or 0
        if max_uses and (promo.get("used") or 0) >= max_uses:
            return {"ok": False, "error": "This promo has reached its usage limit."}

        plan_restriction = promo.get("plan_restriction")
        if plan_restriction:
            allowed = {p.strip() for p in plan_restriction.split(",") if p.strip()}
            if plan not in allowed:
                return {"ok": False, "error": f"This promo isn't valid for the {plan} plan."}

        min_order = promo.get("min_order_bdt") or 0
        if base_bdt < min_order:
            return {"ok": False, "error": f"Minimum order for this promo is {min_order}৳."}

        per_user_limit = promo.get("per_user_limit") or 1
        cur = await db.execute(qs(
            "SELECT COUNT(*) FROM promo_redemptions WHERE code=? AND user_id=?"
        ), (code, user_id))
        used_by_user = (await cur.fetchone())[0]
        if used_by_user >= per_user_limit:
            return {"ok": False, "error": "You've already used this promo the max number of times."}

        if promo.get("first_order_only"):
            cur = await db.execute(qs(
                "SELECT COUNT(*) FROM orders WHERE user_id=? AND status='delivered'"
            ), (user_id,))
            prior_orders = (await cur.fetchone())[0]
            if prior_orders > 0:
                return {"ok": False, "error": "This promo is for first-time orders only."}

    pct = promo["discount_pct"]
    disc_bdt = max(0, round(base_bdt * (100 - pct) / 100))
    disc_stars = max(0, round(base_stars * (100 - pct) / 100))
    return {"ok": True, "bdt": disc_bdt, "stars": disc_stars, "pct": pct, "code": code}


async def record_redemption(code: str, user_id: int, bdt_before: int, bdt_after: int,
                             order_id: int | None = None):
    """Increment the promo's usage counter and log the redemption. Call
    this once per checkout the moment the discount is actually committed
    to a real order/invoice — not just when the code is validated, so
    someone re-checking a code's validity repeatedly doesn't burn uses."""
    async with get_db() as db:
        await db.execute(qs("UPDATE promos SET used = used + 1 WHERE code=?"), (code,))
        await db.execute(qs("""
            INSERT INTO promo_redemptions(code, user_id, order_id, bdt_before, bdt_after)
            VALUES (?,?,?,?,?)
        """), (code, user_id, order_id, bdt_before, bdt_after))
        await db.commit()

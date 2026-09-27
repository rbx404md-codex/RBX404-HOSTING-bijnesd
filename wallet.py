# wallet.py
# A real wallet ledger. Before this, referral payouts (and any future
# admin balance adjustment) did a raw
#   UPDATE users SET balance_stars = balance_stars + ? WHERE user_id=?
# with no record of why, when, or by whom — wallet_transactions existed
# in the schema (migration 2) but nothing ever wrote to it. Every balance
# change should go through credit()/debit() here instead.

from db import get_db, sql as qs

VALID_CURRENCIES = ("stars", "bdt")


async def credit(user_id: int, currency: str, amount: int, tx_type: str,
                  reference: str | None = None, description: str | None = None,
                  admin_id: int | None = None) -> int:
    """Increase a user's balance by `amount` and log it. Returns the new
    balance. Raises ValueError for a bad currency or a non-positive amount
    — silently accepting either would be exactly the kind of unaudited
    balance change this module exists to prevent."""
    if currency not in VALID_CURRENCIES:
        raise ValueError(f"unknown currency: {currency}")
    if amount <= 0:
        raise ValueError("credit amount must be positive; use debit() to subtract")
    return await _adjust(user_id, currency, amount, tx_type, reference, description, admin_id)


async def debit(user_id: int, currency: str, amount: int, tx_type: str,
                reference: str | None = None, description: str | None = None,
                admin_id: int | None = None) -> int | None:
    """Decrease a user's balance by `amount` and log it. Returns the new
    balance, or None if the user doesn't have enough (in which case
    nothing is changed — never lets a balance go negative)."""
    if currency not in VALID_CURRENCIES:
        raise ValueError(f"unknown currency: {currency}")
    if amount <= 0:
        raise ValueError("debit amount must be positive")
    col = f"balance_{currency}"
    async with get_db() as db:
        cur = await db.execute(qs(f"SELECT {col} FROM users WHERE user_id=?"), (user_id,))
        row = await cur.fetchone()
        before = row[0] if row else 0
        if before < amount:
            return None
    return await _adjust(user_id, currency, -amount, tx_type, reference, description, admin_id)


async def _adjust(user_id: int, currency: str, signed_amount: int, tx_type: str,
                  reference: str | None, description: str | None,
                  admin_id: int | None) -> int:
    col = f"balance_{currency}"
    async with get_db() as db:
        cur = await db.execute(qs(f"SELECT {col} FROM users WHERE user_id=?"), (user_id,))
        row = await cur.fetchone()
        before = row[0] if row else 0
        after = before + signed_amount
        await db.execute(qs(f"UPDATE users SET {col} = ? WHERE user_id=?"), (after, user_id))
        await db.execute(qs("""
            INSERT INTO wallet_transactions(
                user_id, type, currency, amount, balance_before, balance_after,
                reference, description, admin_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """), (user_id, tx_type, currency, signed_amount, before, after,
               reference, description, admin_id))
        await db.commit()
        return after


async def history(user_id: int, limit: int = 20) -> list[dict]:
    async with get_db() as db:
        cur = await db.execute(qs("""
            SELECT * FROM wallet_transactions WHERE user_id=?
            ORDER BY id DESC LIMIT ?
        """), (user_id, limit))
        return [dict(r) for r in await cur.fetchall()]

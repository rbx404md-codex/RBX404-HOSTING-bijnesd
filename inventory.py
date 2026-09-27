# inventory.py
# Bulk import, stock queries, .ovpn generation, DB backup.

import aiosqlite, shutil, os, re
from datetime import datetime, timezone
from pathlib import Path
from config import (
    DB_PATH, BACKUP_DIR, OVPN_TEMPLATE_PATH, OVPN_OUT_DIR, USE_POSTGRES,
)
from parser import bulk_parse
from db import get_db, sql as qs


async def bulk_import(blob: str) -> dict:
    parsed, errors = bulk_parse(blob)
    inserted, dupes = 0, 0
    async with get_db() as db:
        for a in parsed:
            if USE_POSTGRES:
                cur = await db.execute(
                    qs("SELECT 1 FROM accounts WHERE email=? AND ovpn_user=?"),
                    (a["email"], a["ovpn_user"]),
                )
                if await cur.fetchone():
                    dupes += 1; continue
                await db.execute(qs("""
                    INSERT INTO accounts
                    (email,password,ovpn_user,ovpn_pass,plan,country,expire_date,days_left,
                     auto_renew,status,license,pptp,raw_line)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """), (
                    a["email"], a["password"], a["ovpn_user"], a["ovpn_pass"], a["plan"],
                    a["country"], a["expire_date"], a["days_left"],
                    bool(a["auto_renew"]), a["status"], a["license"], a["pptp"],
                    f'{a["email"]}:{a["password"]} | OVPNUser={a["ovpn_user"]} | OVPNPass={a["ovpn_pass"]} | Plan={a["plan"]} | Expire={a["expire_date"]}',
                ))
            else:
                cur = await db.execute(
                    "SELECT 1 FROM accounts WHERE email=? AND ovpn_user=?",
                    (a["email"], a["ovpn_user"]),
                )
                if await cur.fetchone():
                    dupes += 1; continue
                await db.execute("""
                    INSERT INTO accounts
                    (email,password,ovpn_user,ovpn_pass,plan,country,expire_date,days_left,
                     auto_renew,status,license,pptp,raw_line)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    a["email"], a["password"], a["ovpn_user"], a["ovpn_pass"], a["plan"],
                    a["country"], a["expire_date"], a["days_left"], a["auto_renew"],
                    a["status"], a["license"], a["pptp"],
                    f'{a["email"]}:{a["password"]} | OVPNUser={a["ovpn_user"]} | OVPNPass={a["ovpn_pass"]} | Plan={a["plan"]} | Expire={a["expire_date"]}',
                ))
            inserted += 1
        await db.commit()
    return {"inserted": inserted, "dupes": dupes, "errors": errors}


async def stock_by_plan() -> dict:
    async with get_db() as db:
        await db.execute(qs("SELECT 1"))
        cur = await db.execute(qs(
            "SELECT plan, COUNT(*) FROM accounts WHERE status='ACTIVE' GROUP BY plan"
        ))
        return {p: c for p, c in await cur.fetchall()}


async def countries() -> list:
    async with get_db() as db:
        cur = await db.execute(qs(
            "SELECT DISTINCT country FROM accounts WHERE status='ACTIVE' ORDER BY country"
        ))
        return [r[0] for r in await cur.fetchall()]


async def reserve_one(plan: str, country: str | None = None, user_id: int | None = None,
                       reservation_minutes: int | None = None, max_attempts: int = 5):
    """Atomically claim one ACTIVE account for `plan`, moving it to
    RESERVED (not straight to SOLD) with an expiry timestamp.

    Two things this fixes vs. the original version:
    1. Race safety: checks the claiming UPDATE's rowcount and retries
       against the next candidate on a lost race, instead of ever
       returning a row nobody actually locked (previously two concurrent
       buyers could both be handed the same account).
    2. Reservation leaks: an abandoned checkout used to leave the account
       stuck as SOLD-to-nobody forever. Now it goes to RESERVED with
       reservation_expires_at, and job_release_expired_reservations()
       puts it back to ACTIVE once that passes.
    """
    from config import RESERVATION_MINUTES, USE_POSTGRES
    if reservation_minutes is None:
        reservation_minutes = RESERVATION_MINUTES

    async with get_db() as db:
        for _ in range(max_attempts):
            q = "SELECT * FROM accounts WHERE status='ACTIVE' AND plan=?"
            params = [plan]
            if country:
                q += " AND country=?"
                params.append(country)
            q += " ORDER BY expire_date DESC LIMIT 1"
            cur = await db.execute(qs(q), tuple(params))
            row = await cur.fetchone()
            if not row:
                return None
            rowd = dict(row)
            if USE_POSTGRES:
                upd = await db.execute(qs("""
                    UPDATE accounts SET status='RESERVED', reserved_by=?,
                           reserved_at=NOW(),
                           reservation_expires_at=NOW() + make_interval(mins => ?)
                    WHERE id=? AND status='ACTIVE'
                """), (user_id, reservation_minutes, rowd["id"]))
            else:
                upd = await db.execute(qs("""
                    UPDATE accounts SET status='RESERVED', reserved_by=?,
                           reserved_at=CURRENT_TIMESTAMP,
                           reservation_expires_at=datetime('now', ?)
                    WHERE id=? AND status='ACTIVE'
                """), (user_id, f"{reservation_minutes} minutes", rowd["id"]))
            if upd.rowcount == 1:
                await db.commit()
                rowd["status"] = "RESERVED"
                return rowd
            # Someone else claimed this exact row between our SELECT and
            # UPDATE — don't return it, try the next candidate.
            await db.commit()
        return None


async def finalize_sale(account_id: int, user_id: int) -> bool:
    """Turn a RESERVED account into SOLD once payment is confirmed.

    Returns False if the reservation already lapsed (expired and was
    released back to ACTIVE by the timeout job, or was already finalized
    by another handler) between checkout and payment confirmation —
    callers MUST NOT deliver credentials when this returns False.
    """
    async with get_db() as db:
        upd = await db.execute(qs("""
            UPDATE accounts SET status='SOLD', sold_to=?, sold_at=CURRENT_TIMESTAMP
            WHERE id=? AND status='RESERVED'
        """), (user_id, account_id))
        await db.commit()
        return upd.rowcount == 1


async def release_reserved(account_id: int):
    async with get_db() as db:
        await db.execute(
            qs("""UPDATE accounts SET status='ACTIVE', sold_to=NULL, sold_at=NULL,
                  reserved_by=NULL, reserved_at=NULL, reservation_expires_at=NULL
                  WHERE id=?"""),
            (account_id,),
        )
        await db.commit()


def _render_ovpn(template: str, ovpn_user: str, ovpn_pass: str) -> str:
    out = template
    if "{{OVPN_USER}}" in out:
        out = out.replace("{{OVPN_USER}}", ovpn_user).replace("{{OVPN_PASS}}", ovpn_pass)
    elif "auth-user-pass" in out:
        out = re.sub(
            r"auth-user-pass\s*\n.*?\n.*?\n",
            f"auth-user-pass\n{ovpn_user}\n{ovpn_pass}\n",
            out, count=1, flags=re.S,
        )
    return out


def generate_ovpn(account: dict, out_dir: str | None = None) -> str:
    out_dir = out_dir or OVPN_OUT_DIR
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    tpl_path = Path(OVPN_TEMPLATE_PATH)
    if not tpl_path.exists():
        tpl = (
            "client\ndev tun\nproto udp\nremote 127.0.0.1 1194\n"
            "resolv-retry infinite\nnobind\npersist-key\npersist-tun\n"
            "auth-user-pass\ncipher AES-256-GCM\nauth SHA256\n"
        )
    else:
        tpl = tpl_path.read_text()
    rendered = _render_ovpn(tpl, account["ovpn_user"], account["ovpn_pass"])
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", account["email"])[:40]
    path = Path(out_dir) / f"{safe}_{account['id']}.ovpn"
    path.write_text(rendered)
    return str(path)


def backup_db():
    if USE_POSTGRES:
        return  # pg_dump handled externally
    Path(BACKUP_DIR).mkdir(parents=True, exist_ok=True)
    if os.path.exists(DB_PATH):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        shutil.copy2(DB_PATH, Path(BACKUP_DIR) / f"rbx404_{ts}.sqlite3")
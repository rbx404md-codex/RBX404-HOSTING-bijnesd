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


async def reserve_one(plan: str, country: str | None = None):
    async with get_db() as db:
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
        await db.execute(
            qs("UPDATE accounts SET status='SOLD' WHERE id=? AND status='ACTIVE'"),
            (rowd["id"],),
        )
        await db.commit()
        return rowd


async def release_reserved(account_id: int):
    async with get_db() as db:
        await db.execute(
            qs("UPDATE accounts SET status='ACTIVE', sold_to=NULL, sold_at=NULL WHERE id=?"),
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
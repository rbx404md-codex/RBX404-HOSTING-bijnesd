# migrate_sqlite_to_pg.py — one-shot sqlite → postgres
import asyncio, sqlite3, os
import psycopg

SQLITE = os.getenv("RBX404_DB", "./data/rbx404.sqlite3")
PG     = os.environ["RBX404_PG_DSN"]

TABLES = ["users", "accounts", "orders", "promos", "referrals", "tickets", "warranty", "stock_alerts"]


async def main():
    src = sqlite3.connect(SQLITE)
    src.row_factory = sqlite3.Row
    async with await psycopg.AsyncConnection.connect(PG) as dst:
        async with dst.cursor() as cur:
            for t in TABLES:
                rows = src.execute(f"SELECT * FROM {t}").fetchall()
                if not rows:
                    print(f"{t}: empty"); continue
                cols = rows[0].keys()
                placeholders = ",".join(["%s"] * len(cols))
                collist = ",".join(cols)
                sql = f"INSERT INTO {t} ({collist}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                for r in rows:
                    await cur.execute(sql, tuple(r))
                print(f"{t}: {len(rows)} rows")
        await dst.commit()


if __name__ == "__main__":
    asyncio.run(main())
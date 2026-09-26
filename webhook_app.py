# webhook_app.py
# FastAPI front for PTB webhook. Production entrypoint.

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, HTTPException
from telegram import Update
from telegram.ext import Application

from config import (
    BOT_TOKEN, PUBLIC_URL, WEBHOOK_PATH, WEBHOOK_SECRET,
)
from db import init_db
from main_handlers import register_handlers

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
log = logging.getLogger("rbx404.webhook")

if not PUBLIC_URL:
    raise SystemExit("RBX404_PUBLIC_URL must be set")

ptb: Application | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global ptb
    await init_db()
    ptb = Application.builder().token(BOT_TOKEN).updater(None).build()
    register_handlers(ptb)
    await ptb.initialize()
    await ptb.bot.set_webhook(
        url=f"{PUBLIC_URL}{WEBHOOK_PATH}",
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )
    log.info("webhook set → %s%s", PUBLIC_URL, WEBHOOK_PATH)
    await ptb.start()
    try:
        yield
    finally:
        log.info("shutting down…")
        try:
            await ptb.bot.delete_webhook(drop_pending_updates=False)
        except Exception as e:
            log.warning("delete_webhook failed: %s", e)
        await ptb.stop()
        await ptb.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def health():
    return {"ok": True, "service": "rbx404-vpn-bot"}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")
    data = await request.json()
    update = Update.de_json(data, ptb.bot)
    await ptb.update_queue.put(update)
    return Response(status_code=200)
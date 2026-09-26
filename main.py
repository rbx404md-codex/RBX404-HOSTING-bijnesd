# main.py
# Polling fallback — use only when webhook unavailable.

import logging
from telegram import Update
from telegram.ext import Application
from config import BOT_TOKEN
from db import init_db
from main_handlers import register_handlers

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s :: %(message)s")


async def _post(app):
    await init_db()


def main():
    app = Application.builder().token(BOT_TOKEN).post_init(_post).build()
    register_handlers(app)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
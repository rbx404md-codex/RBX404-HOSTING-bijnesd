# Running the tests

No extra dependencies needed — everything here uses stdlib `unittest`
against your existing `requirements.txt` (aiosqlite, python-telegram-bot).
Each test file points RBX404_DB at its own temp SQLite file, so nothing
touches your real `./data/rbx404.sqlite3`.

    python3 -m unittest discover -s tests -v

Covers: HybridRow (SQLite/Postgres row-access parity), reservation
race-safety + expiry, finalize_sale double-delivery guard, the promo
engine's validation rules, the wallet ledger (credit/debit/insufficient
funds), and referral-payout idempotency under concurrent job runs.

Not covered yet: anything that needs a live Telegram Bot API call
(webhook handlers, invoice sending, admin approve/reject flows) or a
live Postgres instance — those need integration testing against a real
deploy, not unit tests.

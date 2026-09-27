# config.py
# Central config — all env reads happen here once.

import os

BOT_NAME = "🔒 RBX404 VPN Premium Store"

BOT_TOKEN       = os.getenv("RBX404_BOT_TOKEN", "PASTE_BOT_TOKEN_HERE")
ADMIN_IDS       = [int(x) for x in os.getenv("RBX404_ADMIN_IDS", "0").split(",") if x.strip()]
LOG_CHANNEL_ID  = int(os.getenv("RBX404_LOG_CHANNEL", "0"))

FORCE_JOIN_CHANNELS = [
    {"id": "@SkillShareBDCommunity", "url": "https://t.me/SkillShareBDCommunity"},
    {"id": "@BlackoutZoneRBX404",    "url": "https://t.me/BlackoutZoneRBX404"},
]

BKASH_NUMBER = os.getenv("RBX404_BKASH", "01XXXXXXXXX")
NAGAD_NUMBER = os.getenv("RBX404_NAGAD", "01XXXXXXXXX")

PRICING = {
    "1mo": {"bdt": 50,  "stars": 5,  "label": "1 Month"},
    "3mo": {"bdt": 150, "stars": 15, "label": "3 Months"},
    "6mo": {"bdt": 300, "stars": 30, "label": "6 Months"},
    "1yr": {"bdt": 400, "stars": 45, "label": "1 Year"},
}

WARRANTY_DAYS        = 7
REFERRAL_STARS       = 5
REFERRAL_PAYOUT_DAYS = 7

RESERVATION_MINUTES  = int(os.getenv("RBX404_RESERVATION_MINUTES", "15"))

DB_PATH            = os.getenv("RBX404_DB", "./data/rbx404.sqlite3")
BACKUP_DIR         = os.getenv("RBX404_BACKUP", "./backups")
OVPN_TEMPLATE_PATH = os.getenv("RBX404_OVPN_TPL", "./ovpn/template.ovpn")
OVPN_OUT_DIR       = os.getenv("RBX404_OVPN_OUT", "./data/ovpn_out")

USE_POSTGRES = os.getenv("RBX404_USE_PG", "0") == "1"

PUBLIC_URL      = os.getenv("RBX404_PUBLIC_URL", "").rstrip("/")
WEBHOOK_PATH    = os.getenv("RBX404_WEBHOOK_PATH", "/webhook/rbx404")
WEBHOOK_SECRET  = os.getenv("RBX404_WEBHOOK_SECRET", "CHANGE_ME")
PORT            = int(os.getenv("RBX404_PORT", "8080"))
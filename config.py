import os
from dotenv import load_dotenv

load_dotenv()

# ── Polymarket Credentials ──────────────────────────────
POLY_API_KEY        = os.getenv("POLYMARKET_API_KEY")
POLY_API_SECRET     = os.getenv("POLYMARKET_API_SECRET")
POLY_PASSPHRASE     = os.getenv("POLYMARKET_PASSPHRASE")
POLY_PRIVATE_KEY    = os.getenv("POLYMARKET_PRIVATE_KEY")
WALLET_ADDRESS      = os.getenv("FOUNDER_ADDRESS", "")
SIGNATURE_TYPE      = int(os.getenv("SIGNATURE_TYPE", "2"))
# ── Telegram ────────────────────────────────────────────
TELEGRAM_TOKEN      = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_USER_ID    = int(os.getenv("ALLOWED_CHAT_ID", "0"))

# ── Trading Settings ────────────────────────────────────
COINS               = ["ETH", "SOL", "XRP"]
BASE_BET            = 2.0        # Starting bet $2
MAX_STEPS           = 7          # Max Martingale steps
BET_INTERVAL        = 300        # 5 minutes in seconds
BALANCE_LOW_ALERT   = 50.0       # Alert when balance < $50

# ── Strategy Settings (3-Candle Pattern) ────────────────
STRATEGY_CANDLES    = 3          # Wait for 3 identical candles
STRATEGY_TYPE       = "trend"    # "trend" (follow 3 candles) or "reversal" (bet opposite)
STRATEGY_INTERVAL   = "15m"       # "5m" or "15m"
CANDLE_SOURCE       = "POLYMARKET" # "BINANCE" or "POLYMARKET"

# ── Martingale Table ────────────────────────────────────
def get_bet_amount(step: int) -> float:
    """Returns bet amount for given Martingale step (1-indexed)."""
    return BASE_BET * (2 ** (step - 1))

MARTINGALE_TABLE = {i: get_bet_amount(i) for i in range(1, MAX_STEPS + 1)}
# {1: 2, 2: 4, 3: 8, 4: 16, 5: 32, 6: 64, 7: 128}

# ── Backup / Crash Settings ─────────────────────────────
RESTART_DELAY       = 30         # seconds before auto-restart
MAX_CRASHES         = 3          # stop after 3 crashes

# ── Price Feed ──────────────────────────────────────────
PRICE_UPDATE_SEC    = 1          # terminal price update interval
PRICE_AUTO_TG_MIN   = 1          # telegram auto price interval (minutes)

# ── Logging ─────────────────────────────────────────────
LOG_DIR             = "logs"
CSV_LOG_FILE        = f"{LOG_DIR}/trades.csv"

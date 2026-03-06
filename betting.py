"""
betting.py
Martingale logic, bet placement via Polymarket CLOB API, CSV logging.
"""

import csv
import os
import random
import time
from datetime import datetime
from dataclasses import dataclass, field

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType

import config
from market_finder import get_best_market, get_market_tokens

# ── Setup CSV log ────────────────────────────────────────
os.makedirs(config.LOG_DIR, exist_ok=True)
if not os.path.exists(config.CSV_LOG_FILE):
    with open(config.CSV_LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "coin", "direction", "step", "amount", "result", "pnl"])


# ── Bet Result ───────────────────────────────────────────
@dataclass
class BetResult:
    coin:      str
    direction: str   # "UP" or "DOWN"
    step:      int
    amount:    float
    result:    str   # "WIN" or "LOSS" or "PENDING"
    pnl:       float = 0.0
    market_id: str   = ""
    timestamp: str   = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


# ── Martingale State (per coin) ──────────────────────────
class MartingaleState:
    def __init__(self, coin: str):
        self.coin       = coin
        self.step       = 1
        self.session_pnl = 0.0
        self.history: list[str] = []   # e.g. ["UP✅", "DOWN❌"]
        self.active     = True
        self.stopped    = False        # True after 7-step hard stop

    def next_bet_amount(self) -> float:
        return config.MARTINGALE_TABLE[self.step]

    def on_win(self, pnl: float):
        self.history.append(f"{'UP' if self._last_dir == 'UP' else 'DOWN'}✅")
        self.session_pnl += pnl
        self.step = 1
        self.stopped = False

    def on_loss(self, amount: float):
        self.history.append(f"{'UP' if self._last_dir == 'UP' else 'DOWN'}❌")
        self.session_pnl -= amount
        if self.step >= config.MAX_STEPS:
            self.stopped = True
            self.active  = False
        else:
            self.step += 1

    def set_direction(self, direction: str):
        self._last_dir = direction

    def last_5(self) -> str:
        last = self.history[-5:] if len(self.history) >= 5 else self.history
        return " ".join(last)

    def reset(self):
        self.step        = 1
        self.stopped     = False
        self.active      = True
        self.session_pnl = 0.0
        self.history     = []


# ── Global State ─────────────────────────────────────────
states: dict[str, MartingaleState] = {
    coin: MartingaleState(coin) for coin in config.COINS
}

# Virtual Balance Store
virtual_balance = config.VIRTUAL_BALANCE


# ── CLOB Client ──────────────────────────────────────────
_client: ClobClient | None = None

def get_client() -> ClobClient:
    global _client
    if _client is None:
        from py_clob_client.clob_types import ApiCreds
        from dotenv import load_dotenv
        import os
        import config
        load_dotenv()
        
        # Re-fetch just in case config module loaded them wrongly
        api_key = os.getenv("POLYMARKET_API_KEY")
        api_secret = os.getenv("POLYMARKET_API_SECRET")
        api_passphrase = os.getenv("POLYMARKET_PASSPHRASE")
        priv_key = os.getenv("POLYMARKET_PRIVATE_KEY")
        wallet = os.getenv("FOUNDER_ADDRESS")
        sig_type = int(os.getenv("SIGNATURE_TYPE", "2"))

        _client = ClobClient(
            host="https://clob.polymarket.com",
            key=priv_key,
            chain_id=137,  # Polygon
            signature_type=sig_type,
            funder=wallet if sig_type == 1 else None,
            creds=ApiCreds(
                api_key=api_key,
                api_secret=api_secret,
                api_passphrase=api_passphrase,
            )
        )
    return _client


def get_balance() -> float:
    """Fetch current balance (Virtual or Real USDC)."""
    if config.PAPER_TRADING:
        return virtual_balance
        
    try:
        from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
        client = get_client()
        result = client.get_balance_allowance(
            BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        )
        return float(result.get("balance", 0))
    except Exception as e:
        print(f"[Betting] Balance fetch error: {e}")
        return 0.0


def place_bet(coin: str, direction: str, amount: float | None = None) -> BetResult | None:
    """
    Place a bet on Polymarket for given coin & direction.
    direction: "UP" or "DOWN"
    amount: override amount (optional, uses Martingale step by default)
    """
    state = states[coin]
    if not state.active:
        print(f"[Betting] {coin} is stopped (7-step limit reached)")
        return None

    bet_amount = amount if amount else state.next_bet_amount()
    state.set_direction(direction)

    # Find market
    market = get_best_market(coin)
    if not market:
        print(f"[Betting] No market found for {coin}")
        return None

    up_token, down_token = get_market_tokens(market)
    token_id = up_token if direction == "UP" else down_token
    if not token_id:
        print(f"[Betting] No token found for {coin} {direction}")
        return None

    market_id = market.get("condition_id", "")

    # Place order via CLOB
    if not config.PAPER_TRADING:
        try:
            client = get_client()
            order = client.create_and_post_order(OrderArgs(
                token_id=token_id,
                price=0.5,          # 50/50 market price
                size=bet_amount,
                side="BUY",
            ))
            print(f"[Betting] ✅ Real Order placed: {coin} {direction} ${bet_amount:.2f}")
        except Exception as e:
            print(f"[Betting] Order error for {coin}: {e}")
            return None
    else:
        print(f"[Betting] 🧪 Paper Bet (Simulated): {coin} {direction} ${bet_amount:.2f}")

    result = BetResult(
        coin=coin,
        direction=direction,
        step=state.step,
        amount=bet_amount,
        result="PENDING",
        market_id=market_id,
    )
    return result


def record_result(coin: str, direction: str, won: bool, amount: float):
    """Call after market resolves to update Martingale state and Virtual Balance."""
    global virtual_balance
    state = states[coin]
    
    if won:
        pnl = amount * 0.95  # ~95% payout after fees
        state.on_win(pnl)
        if config.PAPER_TRADING:
            virtual_balance += pnl
        _log_csv(coin, direction, state.step, amount, "WIN", pnl)
        return "WIN", pnl
    else:
        state.on_loss(amount)
        if config.PAPER_TRADING:
            virtual_balance -= amount
        _log_csv(coin, direction, state.step, amount, "LOSS", -amount)
        return "LOSS", -amount


def _log_csv(coin, direction, step, amount, result, pnl):
    with open(config.CSV_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            coin, direction, step, amount, result, round(pnl, 2),
            "PAPER" if config.PAPER_TRADING else "REAL"
        ])


def reset_all_martingales():
    """Resets all coin Martingale states and Virtual Balance."""
    global virtual_balance
    for coin in states:
        states[coin].reset()
    if config.PAPER_TRADING:
        virtual_balance = config.VIRTUAL_BALANCE
    return True


def simulate_result() -> bool:
    """50/50 random result for testing."""
    return random.choice([True, False])
import json
import threading
import websocket
import requests
import time
from datetime import datetime
import config
from config import COINS

# Global price store
prices: dict[str, float] = {coin: 0.0 for coin in COINS}

# Global candle store
candles: dict[str, list[dict]] = {coin: [] for coin in COINS}

SYMBOL_MAP = {
    "ETH": "ethusdt",
    "SOL": "solusdt",
    "XRP": "xrpusdt",
}

_ws_app = None

def fetch_klines(coin: str, interval: str = "5m", limit: int = 10):
    """Fetch recent klines (candles) from Binance (fallback)."""
    try:
        symbol = SYMBOL_MAP[coin].upper()
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        
        formatted = []
        for c in data[:-1]:
            op = float(c[1])
            cl = float(c[4])
            color = "GREEN" if cl >= op else "RED"
            formatted.append({
                "time": datetime.fromtimestamp(c[0]/1000).strftime("%H:%M"),
                "open": op,
                "close": cl,
                "color": color
            })
        return formatted
    except Exception as e:
        print(f"[PriceFeed] Binance fetch error for {coin}: {e}")
        return []

from concurrent.futures import ThreadPoolExecutor

def fetch_polymarket_candles(coin: str, interval: str = "15m", limit: int = 5):
    """Fetch recent outcomes from Polymarket events for the coin (Early detection)."""
    try:
        results = []
        now = int(time.time())
        step = 300 if interval == "5m" else 900
        
        # Restore: Look at last 6 intervals to ensure 5 candles for UI
        for i in range(1, 7):
            ts = ((now // step) - i) * step
            slug = f"{coin.lower()}-updown-{interval}-{ts}"
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            
            try:
                r = requests.get(url, timeout=5) # Reduced timeout to 5s
                if r.status_code == 200:
                    data = r.json()
                    if data and len(data) > 0 and data[0].get("markets"):
                        m = data[0]["markets"][0]
                        outcomes_raw = m.get("outcomes")
                        prices_raw = m.get("outcomePrices")
                        
                        if outcomes_raw and prices_raw:
                            outcomes = json.loads(outcomes_raw)
                            prices = json.loads(prices_raw)
                            
                            winner_idx = -1
                            if "1" in prices:
                                winner_idx = prices.index("1")
                            elif now > (ts + step): # Past expiration
                                float_prices = [float(p) for p in prices]
                                max_p = max(float_prices)
                                if max_p > 0.8:
                                    winner_idx = float_prices.index(max_p)

                            if winner_idx != -1:
                                winner = outcomes[winner_idx].lower()
                                color = "GREEN" if winner in ["up", "yes", "higher"] else "RED"
                                results.append({
                                    "time": datetime.fromtimestamp(ts).strftime("%H:%M"),
                                    "color": color,
                                    "source": "Polymarket"
                                })
                                if len(results) >= limit:
                                    break
            except Exception:
                pass
        
        return results[::-1]
    except Exception as e:
        print(f"[PriceFeed] Polymarket fetch error for {coin}: {e}")
        return []

def update_all_candles():
    """Update candles for all tracked coins in PARALLEL."""
    def _update(coin):
        if config.CANDLE_SOURCE == "POLYMARKET":
            return coin, fetch_polymarket_candles(coin, interval=config.STRATEGY_INTERVAL)
        else:
            return coin, fetch_klines(coin, interval=config.STRATEGY_INTERVAL)

    with ThreadPoolExecutor(max_workers=len(COINS)) as executor:
        results = list(executor.map(_update, COINS))
        for coin, data in results:
            if data:
                candles[coin] = data


def _on_message(ws, message):
    data = json.loads(message)
    if "data" in data:
        data = data["data"]
    symbol = data.get("s", "").upper()
    price  = float(data.get("c", 0))
    for coin, sym in SYMBOL_MAP.items():
        if sym.upper() == symbol:
            prices[coin] = price


def _on_error(ws, error):
    pass


def _on_close(ws, *args):
    import time; time.sleep(5)
    start()


def _on_open(ws):
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": [f"{s}@ticker" for s in SYMBOL_MAP.values()],
        "id": 1
    }))


def _candle_loop():
    """Adaptive Background loop: Fast polls near market closure."""
    while True:
        try:
            update_all_candles()
        except Exception as e:
            print(f"[PriceFeed] Error in candle loop: {e}")
        
        # Adaptive sleep: 
        # Fast poll (2s) if near 5m mark (which includes 15m mark).
        # We poll frequently 10s before and 30s after the mark.
        now = int(time.time())
        rem = now % 300 
        if rem > 290 or rem < 30: 
            time.sleep(2)
        else:
            time.sleep(15)

def start():
    """Start Binance WebSocket price feed and candle loop."""
    global _ws_app
    streams = "/".join([f"{s}@ticker" for s in SYMBOL_MAP.values()])
    url = f"wss://stream.binance.com:9443/stream?streams={streams}"
    _ws_app = websocket.WebSocketApp(
        url,
        on_open=_on_open,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )
    # Price feed thread
    ws_thread = threading.Thread(target=_ws_app.run_forever, daemon=True)
    ws_thread.start()
    
    # Candle loop thread
    candle_thread = threading.Thread(target=_candle_loop, daemon=True)
    candle_thread.start()
    
    print("[PriceFeed] All feeds started ✅")


def get_price(coin: str) -> float:
    return prices.get(coin, 0.0)


def get_all_prices() -> dict[str, float]:
    return dict(prices)

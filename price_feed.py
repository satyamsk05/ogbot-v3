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

def fetch_polymarket_candles(coin: str, interval: str = "15m", limit: int = 5):
    """Fetch recent outcomes from Polymarket events for the coin."""
    try:
        results = []
        now = int(time.time())
        step = 300 if interval == "5m" else 900
        
        # Look back up to 10 markets to find 'limit' resolved ones
        for i in range(10):
            ts = ((now // step) - i) * step
            slug = f"{coin.lower()}-updown-{interval}-{ts}"
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    if data and len(data) > 0 and data[0].get("markets"):
                        m = data[0]["markets"][0]
                        outcomes_raw = m.get("outcomes")
                        prices_raw = m.get("outcomePrices")
                        
                        if outcomes_raw and prices_raw:
                            outcomes = json.loads(outcomes_raw)
                            prices = json.loads(prices_raw)
                            
                            if "1" in prices:
                                winner = outcomes[prices.index("1")].lower()
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
            time.sleep(0.05)
        
        return results[::-1]
    except Exception as e:
        print(f"[PriceFeed] Polymarket fetch error for {coin}: {e}")
        return []

def update_all_candles():
    """Update candles for all tracked coins based on config."""
    for coin in COINS:
        if config.CANDLE_SOURCE == "POLYMARKET":
            data = fetch_polymarket_candles(coin, interval=config.STRATEGY_INTERVAL)
        else:
            data = fetch_klines(coin, interval=config.STRATEGY_INTERVAL)
            
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
    print(f"[PriceFeed] WebSocket error: {error}")


def _on_close(ws, *args):
    print("[PriceFeed] WebSocket closed — reconnecting in 5s...")
    import time; time.sleep(5)
    start()


def _on_open(ws):
    streams = "/".join([f"{s}@ticker" for s in SYMBOL_MAP.values()])
    ws.send(json.dumps({
        "method": "SUBSCRIBE",
        "params": [f"{s}@ticker" for s in SYMBOL_MAP.values()],
        "id": 1
    }))


def _candle_loop():
    """Background loop to update candles periodically."""
    print("[PriceFeed] Candle background loop started ✅")
    while True:
        try:
            update_all_candles()
        except Exception as e:
            print(f"[PriceFeed] Error in candle loop: {e}")
        
        # Update every 60 seconds (or more frequently if needed)
        time.sleep(60)

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

"""
market_finder.py
Find active 5-minute UP/DOWN markets for ETH, SOL, XRP on Polymarket.
"""

import requests
from config import COINS

GAMMA_API = "https://gamma-api.polymarket.com"


def search_markets(coin: str) -> list[dict]:
    """Search Polymarket for active short-term markets for a coin."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/markets",
            params={
                "active": "true",
                "closed": "false",
                "keyword": coin,
                "limit": 20,
            },
            timeout=10,
        )
        resp.raise_for_status()
        markets = resp.json()
        # Filter for 5-min style markets
        short_term = []
        keywords = ["5 min", "5min", "5-min", "minute", "up or down", "higher or lower"]
        for m in markets:
            title = m.get("question", "").lower()
            if any(kw in title for kw in keywords):
                short_term.append(m)
        return short_term
    except Exception as e:
        print(f"[MarketFinder] Error searching {coin}: {e}")
        return []


def get_best_market(coin: str) -> dict | None:
    """Return the best available 5-min market for a coin."""
    markets = search_markets(coin)
    if not markets:
        print(f"[MarketFinder] No 5-min market found for {coin}")
        return None
    # Pick market with highest liquidity
    best = max(markets, key=lambda m: float(m.get("liquidity", 0)))
    return best


def get_market_tokens(market: dict) -> tuple[str, str]:
    """
    Returns (up_token_id, down_token_id) from a market.
    Polymarket binary markets have 2 outcome tokens.
    """
    tokens = market.get("tokens", [])
    up_token   = None
    down_token = None
    for t in tokens:
        outcome = t.get("outcome", "").upper()
        if outcome in ["YES", "UP", "HIGHER"]:
            up_token = t.get("token_id")
        elif outcome in ["NO", "DOWN", "LOWER"]:
            down_token = t.get("token_id")
    return up_token, down_token


def get_all_active_markets() -> dict[str, dict | None]:
    """Returns best market for each coin."""
    result = {}
    for coin in COINS:
        result[coin] = get_best_market(coin)
    return result

"""
main.py
Main bot loop — terminal dashboard, auto betting, crash recovery.
"""

import asyncio
import os
import random
import sys
import time
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich import box

import config
import price_feed
import betting
from betting import states, place_bet, record_result, get_balance, simulate_result
import telegram_bot as tg

console = Console()

# ── Crash counter ────────────────────────────────────────
crash_count = 0


# ── Terminal Dashboard ───────────────────────────────────
# ── Terminal Dashboard ───────────────────────────────────
def build_dashboard() -> Table:
    p = price_feed.get_all_prices()
    bal = get_balance()

    # Outer table using DOUBLE box for premium feel
    table = Table(
        box=box.DOUBLE,
        style="cyan",
        show_header=False,
        expand=True,
    )
    table.add_column("Content", justify="left")

    # ── Header ──
    table.add_row("[bold cyan]🤖 POLYMARKET VIP BOT — LIVE[/bold cyan]")
    table.add_section()

    # ── Prices Section ──
    price_lines = []
    for coin in config.COINS:
        pr = p.get(coin, 0.0)
        decimals = 4 if coin == "XRP" else 2
        price_lines.append(f"  [bold white]{coin}[/bold white]: [green]${pr:,.{decimals}f}[/green]")
    table.add_row("\n".join(price_lines))
    table.add_section()

    # ── Martingale status Section ──
    for coin in config.COINS:
        s = states[coin]
        status = "[bold green]🟢 RUN[/bold green]" if s.active and not s.stopped else \
                 ("[bold red]🛑 STOP[/bold red]" if s.stopped else "[bold yellow]⏸ PAUSE[/bold yellow]")
        
        # Format history string
        history = s.last_5()
        
        table.add_row(
            f"  {status} [bold]{coin:4}[/bold] Step [yellow]{s.step}/{config.MAX_STEPS}[/yellow] "
            f"Bet: [bold]${s.next_bet_amount():.0f}[/bold] P&L: [green]${s.session_pnl:.2f}[/green]\n"
            f"  History: {history}"
        )
    table.add_section()

    # ── Net P&L + Balance Section ──
    total_pnl = sum(states[c].session_pnl for c in config.COINS)
    mode_label = tg.bot_mode.upper() if hasattr(tg, "bot_mode") else "AUTO"
    bal_label = "V-Balance" if config.PAPER_TRADING else "Balance"
    pnl_style = "green" if total_pnl >= 0 else "red"
    
    table.add_row(
        f"  [bold yellow]NET P&L:[/bold yellow] [{pnl_style}]${total_pnl:.2f}[/{pnl_style}]  |  "
        f"[bold blue]{bal_label}:[/bold blue] ${bal:.2f}  |  "
        f"[bold magenta]MODE:[/bold magenta] {mode_label}"
    )
    table.add_section()

    # ── Last 5 candles Section ──
    source_label = f"--- [bold white]Candles ({config.CANDLE_SOURCE})[/bold white] ---"
    table.add_row(f"  {source_label}")
    for coin in config.COINS:
        coin_candles = price_feed.candles.get(coin, [])
        history_icons = []
        for c in coin_candles[-5:]:
            icon = "🟢" if c["color"] == "GREEN" else "🔴"
            history_icons.append(icon)
        
        history_str = " ".join(history_icons) if history_icons else "Loading data..."
        table.add_row(f"  [bold]{coin:4}[/bold]: {history_str}")

    table.add_row(f"\n  🕐 [grey62]{datetime.now().strftime('%H:%M:%S')}[/grey62]")
    return table


strategy_state = {
    coin: {
        "last_candle_time": "",
        "in_recovery": False,
        "active_side": None,
        "waiting_for_pattern": True
    } for coin in config.COINS
}

# ── Auto Betting Loop (3-Candle Trend Strategy) ──────────
async def auto_bet_loop():
    """
    3-Candle REVERSAL Strategy:
    1. Wait for 3 identical candles (e.g. R-R-R).
    2. Place bet IMMEDIATELY on the 4th candle in the OPPOSITE direction.
    3. If Win: Reset and wait for NEXT 3-candle pattern.
    4. If Loss: Martingale ON NEXT candle (same direction) until Win.
    """
    console.print(f"[bold yellow]📊 Strategy Initialized: {config.STRATEGY_CANDLES}-Candle {config.STRATEGY_TYPE.upper()}[/bold yellow]")
    
    while True:
        if tg.bot_running and tg.bot_mode == "auto":
            # Update candles first
            price_feed.update_all_candles()

            for coin in config.COINS:
                s = states[coin]
                st = strategy_state[coin]

                if not s.active or s.stopped:
                    continue
                if not tg.coin_enabled.get(coin, True):
                    continue

                coin_candles = price_feed.candles.get(coin, [])
                if len(coin_candles) < config.STRATEGY_CANDLES:
                    continue

                last_candle = coin_candles[-1]
                last_time = last_candle['time']

                # Only process once per candle close
                if last_time == st["last_candle_time"]:
                    continue
                
                st["last_candle_time"] = last_time
                
                # ── Step 1: Check Result of existing bet if any ──
                if st["active_side"]:
                    # Result is the color of the candle that just closed
                    won = (st["active_side"] == "UP" and last_candle["color"] == "GREEN") or \
                          (st["active_side"] == "DOWN" and last_candle["color"] == "RED")
                    
                    amount = s.next_bet_amount()
                    outcome, pnl = record_result(coin, st["active_side"], won, amount)

                    if won:
                        await tg.notify_win(coin, amount, pnl)
                        st["in_recovery"] = False
                        st["waiting_for_pattern"] = True
                        st["active_side"] = None
                    else:
                        await tg.notify_loss(coin, amount)
                        st["in_recovery"] = True
                        # st["waiting_for_pattern"] remains False, we bet on next candle
                
                # ── Step 2: Signal Detection / Next Bet ──
                direction = None
                
                if st["waiting_for_pattern"]:
                    # Wait for 3-in-a-row
                    recent_colors = [c['color'] for c in coin_candles[-config.STRATEGY_CANDLES:]]
                    if all(c == "RED" for c in recent_colors):
                        direction = "DOWN" if config.STRATEGY_TYPE == "trend" else "UP"
                    elif all(c == "GREEN" for c in recent_colors):
                        direction = "UP" if config.STRATEGY_TYPE == "trend" else "DOWN"
                    
                    if direction:
                        st["waiting_for_pattern"] = False
                        st["active_side"] = direction
                elif st["in_recovery"]:
                    # Martingale on next candle (stay in same direction)
                    direction = st["active_side"]

                # ── Step 3: Place Bet ──
                if direction:
                    amount = s.next_bet_amount()
                    await tg.notify_bet_placed(coin, direction, amount, s.step)
                    place_bet(coin, direction)

            # Check Balance
            bal = get_balance()
            if bal < config.BALANCE_LOW_ALERT:
                await tg.notify_balance_low(bal)

        # Precise Sleep: Wait until ~1 second after the candle close
        now = time.time()
        interval_secs = 300 if config.STRATEGY_INTERVAL == "5m" else 900
        time_into_interval = now % interval_secs
        sleep_time = interval_secs - time_into_interval + 1 # 1 second after close
        
        # If we are very close to next check, just wait a bit
        if sleep_time < 2: 
            sleep_time = interval_secs + 1
            
        # Limit sleep to max 30s to keep dashboard alive
        final_sleep = min(sleep_time, 30)
        await asyncio.sleep(final_sleep)


# ── Daily Summary ────────────────────────────────────────
async def daily_summary_loop():
    """Send daily P&L summary at midnight."""
    while True:
        now = datetime.now()
        # Sleep until next midnight
        seconds_until_midnight = (
            (24 - now.hour - 1) * 3600 +
            (60 - now.minute - 1) * 60 +
            (60 - now.second)
        )
        await asyncio.sleep(seconds_until_midnight)
        await tg.notify_daily_summary()


# ── Terminal Dashboard Loop ───────────────────────────────
def run_dashboard():
    with Live(build_dashboard(), refresh_per_second=1, console=console) as live:
        while True:
            live.update(build_dashboard())
            time.sleep(1)


# ── Main Entry ───────────────────────────────────────────
def main():
    global crash_count

    console.print("[bold green]🚀 Starting Polymarket Bot...[/bold green]")

    # Start price feed
    price_feed.start()
    time.sleep(2)  # Let WebSocket connect

    # Start Telegram bot
    tg.start_telegram_bot()
    time.sleep(2)

    console.print("[bold green]✅ All systems ready![/bold green]")

    # Start async loops in background
    import threading

    def run_async():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(asyncio.gather(
            auto_bet_loop(),
            daily_summary_loop(),
        ))

    async_thread = threading.Thread(target=run_async, daemon=True)
    async_thread.start()

    # Run terminal dashboard (blocks main thread)
    run_dashboard()


# ── Crash Recovery Wrapper ───────────────────────────────
if __name__ == "__main__":
    while True:
        try:
            main()
        except KeyboardInterrupt:
            console.print("\n[bold red]Bot stopped by user.[/bold red]")
            sys.exit(0)
        except Exception as e:
            crash_count += 1
            console.print(f"[bold red]❌ Crash #{crash_count}: {e}[/bold red]")

            # Notify Telegram
            try:
                import asyncio
                asyncio.run(tg.notify_restart(crash_count))
            except Exception:
                pass

            if crash_count >= config.MAX_CRASHES:
                console.print("[bold red]🛑 Max crashes reached. Bot shutting down.[/bold red]")
                sys.exit(1)

            console.print(f"[yellow]Restarting in {config.RESTART_DELAY}s...[/yellow]")
            time.sleep(config.RESTART_DELAY)

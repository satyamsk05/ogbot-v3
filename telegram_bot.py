"""
telegram_bot.py
Telegram bot — commands, inline buttons, notifications, auto price updates.
"""

import asyncio
import threading
from datetime import datetime

from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
)

import config
import price_feed
import betting
from betting import states, place_bet, record_result, get_balance, simulate_result, reset_all_martingales

# ── Auth decorator ───────────────────────────────────────
def authorized(func):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user and user.id != config.TELEGRAM_USER_ID:
            if update.message:
                await update.message.reply_text("❌ Unauthorized")
            return
        return await func(update, ctx)
    wrapper.__name__ = func.__name__
    return wrapper


# ── Bot mode state ───────────────────────────────────────
bot_running  = True            # Enabled by default as per user request
bot_mode     = "auto"
coin_enabled = {c: True for c in config.COINS}

_app: Application | None = None
_bot: Bot | None         = None
_loop: asyncio.AbstractEventLoop | None = None
_dashboard_msg_id: int | None = None
_latest_event: str | None = None


# ── Thread-Safe Helper ───────────────────────────────────
async def _safe_run(coro):
    """Ensures a coroutine runs on the Telegram event loop."""
    global _loop
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if _loop and _loop.is_running() and current_loop != _loop:
        future = asyncio.run_coroutine_threadsafe(coro, _loop)
        return await asyncio.wrap_future(future)
    return await coro

def get_dashboard_text() -> str:
    """Generates the formatted dashboard status text."""
    p = price_feed.get_all_prices()
    bal = get_balance()
    dt = datetime.now().strftime("%H:%M:%S")
    
    status_str = "🟢 RUNNING" if bot_running else "🔴 STOPPED"
    
    bal_label = "Virtual Balance" if config.PAPER_TRADING else "Balance"
    lines = [
        f"        🤖 *POLYMARKET BOT DASHBOARD*",
        f"        ────────────────────────",
        f"📊 *Status:* `{status_str}` | *Mode:* `{bot_mode.upper()}`",
        f"⚙️📊 Strategy: `{config.STRATEGY_CANDLES}-Candle {config.STRATEGY_TYPE.upper()}`",
        f"💰 *{bal_label}:* `${bal:.2f}` USDC",
        f"🕐 *Last Update:* `{dt}`\n"
    ]

    if _latest_event:
        lines.append(f"🔔 *Latest Event:* {_latest_event}\n")

    lines.extend([
        f"📈 *Live Prices*",
        f"`ETH: ${p['ETH']:,.2f}`",
        f"`SOL: ${p['SOL']:,.2f}`",
        f"`XRP: ${p['XRP']:.4f}`\n",
        f"📋 *Strategy Status*"
    ])
    
    for coin in config.COINS:
        try:
            s = states[coin]
            # Get last 5 candle colors from price_feed
            coin_candles = price_feed.candles.get(coin, [])
            history_str = "".join(["🟢" if c["color"] == "GREEN" else "🔴" for c in coin_candles[-5:]])
            
            from main import strategy_state # Import here to avoid circular
            st = strategy_state.get(coin, {})
            waiting = st.get("waiting_for_pattern", True)
            side    = st.get("active_side", "NONE")
            
            strat_status = "⌛ Waiting" if waiting else f"🎯 Active {side}"
            
            lines.append(f"`{coin}`: {history_str} | {strat_status}")
            lines.append(f"   Step {s.step} | P&L `${s.session_pnl:.2f}`")
        except Exception as e:
            lines.append(f"`{coin}`: Data loading... ({e})")
            
    return "\n".join(lines)


async def update_dashboard(new_event: str | None = None):
    """Edits the existing dashboard message (Thread-Safe)."""
    return await _safe_run(_update_dashboard_logic(new_event))


async def _update_dashboard_logic(new_event: str | None = None):
    """Internal logic for updating the dashboard."""
    global _dashboard_msg_id, _latest_event
    
    if new_event:
        _latest_event = new_event

    text = get_dashboard_text()
    
    if not _bot or not config.TELEGRAM_USER_ID:
        return

    if _dashboard_msg_id:
        try:
            await _bot.edit_message_text(
                chat_id=config.TELEGRAM_USER_ID,
                message_id=_dashboard_msg_id,
                text=text,
                reply_markup=kb_dashboard(),
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            # If message is too old or gone, we fall back to sending a new one
            print(f"[Telegram] Dashboard edit failed: {e}")

    # Send new message
    try:
        msg = await _bot.send_message(
            chat_id=config.TELEGRAM_USER_ID,
            text=text,
            reply_markup=kb_dashboard(),
            parse_mode="Markdown"
        )
        _dashboard_msg_id = msg.message_id
    except Exception as e:
        print(f"[Telegram] Dashboard send failed: {e}")


def kb_dashboard() -> InlineKeyboardMarkup:
    """Inline Dashboard buttons matching the premium design."""
    # Toggle button label based on current state
    ctrl_btn = InlineKeyboardButton("🔴 STOP BOT", callback_data="ctrl_toggle") if bot_running else \
               InlineKeyboardButton("🟢 START BOT", callback_data="ctrl_toggle")
    
    keyboard = [
        [
            InlineKeyboardButton("🔋 STATUS", callback_data="dash_refresh"),
            InlineKeyboardButton("💎 " + ("V-WALLET" if config.PAPER_TRADING else "WALLET"), callback_data="dash_refresh")
        ],
        [
            ctrl_btn
        ],
        [
            InlineKeyboardButton("⚡ MARTINGALE RESET", callback_data="ctrl_reset"),
            InlineKeyboardButton("📊 HISTORY", callback_data="ctrl_history")
        ],
        [
            InlineKeyboardButton("🔄 REFRESH", callback_data="dash_refresh")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


# ── Send helper ──────────────────────────────────────────
async def send(text: str, reply_markup=None):
    """Fallback send helper (Thread-Safe)."""
    return await _safe_run(_send_logic(text, reply_markup))


async def _send_logic(text: str, reply_markup=None):
    """Internal logic for sending a message."""
    if not _bot or not config.TELEGRAM_USER_ID:
        return
    try:
        await _bot.send_message(
            chat_id=config.TELEGRAM_USER_ID,
            text=text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"[Telegram] Send error: {e}")


# ══════════════════════════════════════════════════════════
# ── COMMANDS ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════

@authorized
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Initializes/Resends the dashboard."""
    global _dashboard_msg_id
    # Remove old ReplyKeyboard if exists
    try:
        await update.message.reply_text("🧹 *Cleaning up old menu...*", reply_markup=ReplyKeyboardRemove())
    except:
        pass
    
    msg = await update.message.reply_text(
        get_dashboard_text(),
        parse_mode="Markdown",
        reply_markup=kb_dashboard()
    )
    _dashboard_msg_id = msg.message_id


@authorized
async def cmd_dashboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Force resend dashboard."""
    global _dashboard_msg_id
    msg = await update.message.reply_text(
        get_dashboard_text(),
        parse_mode="Markdown",
        reply_markup=kb_dashboard()
    )
    _dashboard_msg_id = msg.message_id


@authorized
async def cmd_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = False
    await update.message.reply_text(
        "🔴 *Bot Stopped.*",
        parse_mode="Markdown",
        reply_markup=kb_dashboard()
    )


@authorized
async def cmd_pause(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = False
    await update.message.reply_text(
        "⏸ *Bot Paused.*",
        parse_mode="Markdown",
        reply_markup=kb_dashboard()
    )


@authorized
async def cmd_resume(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global bot_running
    bot_running = True
    await update.message.reply_text(
        "🟢 *Bot Resumed!*",
        parse_mode="Markdown",
        reply_markup=kb_dashboard()
    )


@authorized
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # This command will now just show the dashboard
    await cmd_dashboard(update, ctx)


@authorized
async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # This command will now just show the dashboard
    await cmd_dashboard(update, ctx)


@authorized
async def cmd_reset_all(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    reset_all_martingales()
    await update_dashboard("⚡ *All Martingale steps reset!*")


@authorized
async def cmd_price(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # This command will now just show the dashboard
    await cmd_dashboard(update, ctx)


@authorized
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lines = ["📋 *Bet History (Last 5)*\n"]
    for coin in config.COINS:
        s = states[coin]
        h = s.last_5() if s.history else "No bets yet"
        lines.append(f"`{coin}`: {h}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb_dashboard())


@authorized
async def cmd_martingale(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # This command will now just show the dashboard
    await cmd_dashboard(update, ctx)


@authorized
async def cmd_bet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /bet          → show coin selection buttons
    /bet ETH      → show UP/DOWN buttons for ETH
    /bet ETH UP   → confirm button
    /bet ALL      → show UP/DOWN for all coins
    """
    args = ctx.args

    # No args → show coin + direction buttons
    if not args:
        keyboard = []
        for coin in config.COINS:
            keyboard.append([
                InlineKeyboardButton(f"📈 {coin} UP",   callback_data=f"bet_{coin}_UP"),
                InlineKeyboardButton(f"📉 {coin} DOWN", callback_data=f"bet_{coin}_DOWN"),
            ])
        keyboard.append([
            InlineKeyboardButton("📈 ALL UP",   callback_data="bet_ALL_UP"),
            InlineKeyboardButton("📉 ALL DOWN", callback_data="bet_ALL_DOWN"),
        ])
        await update.message.reply_text(
            "🎲 *Manual Bet — Choose direction:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    coin_arg = args[0].upper()

    # /bet ETH → show UP/DOWN
    if len(args) == 1:
        if coin_arg == "ALL":
            await update.message.reply_text(
                "🎲 *ALL Coins — Direction?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 UP (All)",   callback_data="bet_ALL_UP"),
                    InlineKeyboardButton("📉 DOWN (All)", callback_data="bet_ALL_DOWN"),
                ]])
            )
        else:
            await update.message.reply_text(
                f"🎲 *{coin_arg} — Direction?*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📈 UP",   callback_data=f"bet_{coin_arg}_UP"),
                    InlineKeyboardButton("📉 DOWN", callback_data=f"bet_{coin_arg}_DOWN"),
                ]])
            )
        return

    # /bet ETH UP → confirm
    direction = args[1].upper()
    if direction not in ["UP", "DOWN"]:
        await update.message.reply_text("Direction must be UP or DOWN")
        return

    coins_to_bet = config.COINS if coin_arg == "ALL" else [coin_arg]
    for coin in coins_to_bet:
        if coin not in config.COINS:
            continue
        s   = states[coin]
        amt = s.next_bet_amount()
        await update.message.reply_text(
            f"🎯 *{coin} {direction}* — `${amt:.0f}` (Step {s.step})\nConfirm?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{coin}_{direction}_{amt}"),
                InlineKeyboardButton("❌ Cancel",  callback_data="cancel_bet"),
            ]])
        )


@authorized
async def cmd_setbet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /setbet [amount]")
        return
    try:
        config.BASE_BET = float(ctx.args[0])
        for i in range(1, config.MAX_STEPS + 1):
            config.MARTINGALE_TABLE[i] = config.BASE_BET * (2 ** (i - 1))
        await update.message.reply_text(f"✅ Base bet set to `${config.BASE_BET:.2f}`", parse_mode="Markdown", reply_markup=kb_dashboard())
    except ValueError:
        await update.message.reply_text("Invalid amount")


@authorized
async def cmd_setstop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Usage: /setstop [steps]")
        return
    try:
        config.MAX_STEPS = int(ctx.args[0])
        await update.message.reply_text(f"✅ Max steps set to `{config.MAX_STEPS}`", parse_mode="Markdown", reply_markup=kb_dashboard())
    except ValueError:
        await update.message.reply_text("Invalid number")


@authorized
async def cmd_coin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /coin        → show toggle buttons
    /coin ETH OFF → direct toggle
    """
    if not ctx.args:
        buttons = []
        for coin in config.COINS:
            label = f"{coin} ✅" if coin_enabled.get(coin, True) else f"{coin} ❌"
            buttons.append(InlineKeyboardButton(label, callback_data=f"coin_{coin}"))
        await update.message.reply_text(
            "🪙 *Coin ON/OFF Toggle:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([buttons])
        )
        return

    coin   = ctx.args[0].upper()
    toggle = ctx.args[1].upper() if len(ctx.args) > 1 else None
    if coin not in config.COINS:
        await update.message.reply_text(f"Unknown coin: {coin}")
        return
    if toggle:
        states[coin].active  = (toggle == "ON")
        coin_enabled[coin]   = (toggle == "ON")
        
        buttons = []
        for c in config.COINS:
            label = f"{c} ✅" if coin_enabled.get(c, True) else f"{c} ❌"
            buttons.append(InlineKeyboardButton(label, callback_data=f"coin_{c}"))

        await update.message.reply_text(
            f"✅ `{coin}` is now `{toggle}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([buttons])
        )


@authorized
async def cmd_mode(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /mode        → show mode buttons
    /mode auto   → set auto
    """
    global bot_mode
    if not ctx.args:
        auto_label   = "🤖 Auto ✅"  if bot_mode == "auto"   else "🤖 Auto"
        manual_label = "🖐 Manual ✅" if bot_mode == "manual" else "🖐 Manual"
        await update.message.reply_text(
            f"⚙️ *Current Mode: {bot_mode.upper()}*\nSwitch:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(auto_label,   callback_data="mode_auto"),
                InlineKeyboardButton(manual_label, callback_data="mode_manual"),
            ]])
        )
        return
    m = ctx.args[0].lower()
    if m not in ["auto", "manual"]:
        await update.message.reply_text("Mode must be 'auto' or 'manual'")
        return
    bot_mode = m
    auto_label   = "🤖 Auto ✅"  if bot_mode == "auto"   else "🤖 Auto"
    manual_label = "🖐 Manual ✅" if bot_mode == "manual" else "🖐 Manual"
    await update.message.reply_text(
        f"✅ Mode: `{bot_mode.upper()}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(auto_label,   callback_data="mode_auto"),
            InlineKeyboardButton(manual_label, callback_data="mode_manual"),
        ]])
    )


# ══════════════════════════════════════════════════════════
# ── CALLBACK QUERY HANDLER (button clicks) ───────────────
# ══════════════════════════════════════════════════════════

async def handle_buttons(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()

        if query.from_user.id != config.TELEGRAM_USER_ID:
            await query.answer("❌ Unauthorized", show_alert=True)
            return

        data = query.data
        global bot_running, bot_mode

        # ── Dashboard Control ──
        if data == "ctrl_toggle":
            bot_running = not bot_running
            status = "🟢 *Bot Started*" if bot_running else "🔴 *Bot Stopped*"
            await update_dashboard(status)
        elif data == "ctrl_reset":
            reset_all_martingales()
            await update_dashboard("⚡ *Steps Reset to 1*")
        elif data == "ctrl_history":
            await cmd_history(update, ctx)
        elif data == "dash_refresh":
            price_feed.update_all_candles() # Force fresh fetch
            await update_dashboard()

        # ── Mode Switch (from /mode command) ──
        elif data == "mode_auto":
            bot_mode = "auto"
            auto_label   = "🤖 Auto ✅"  if bot_mode == "auto"   else "🤖 Auto"
            manual_label = "🖐 Manual ✅" if bot_mode == "manual" else "🖐 Manual"
            await query.edit_message_text("✅ Mode: *AUTO*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(auto_label,   callback_data="mode_auto"),
                InlineKeyboardButton(manual_label, callback_data="mode_manual"),
            ]]))
            return # Don't refresh dashboard, stay on mode switch

        elif data == "mode_manual":
            bot_mode = "manual"
            auto_label   = "🤖 Auto ✅"  if bot_mode == "auto"   else "🤖 Auto"
            manual_label = "🖐 Manual ✅" if bot_mode == "manual" else "🖐 Manual"
            await query.edit_message_text("✅ Mode: *MANUAL*", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(auto_label,   callback_data="mode_auto"),
                InlineKeyboardButton(manual_label, callback_data="mode_manual"),
            ]]))
            return # Don't refresh dashboard, stay on mode switch

        # ── Coin Toggle (from /coin command) ──
        elif data.startswith("coin_"):
            coin = data.split("_")[1]
            if coin in config.COINS:
                coin_enabled[coin]  = not coin_enabled.get(coin, True)
                states[coin].active = coin_enabled[coin]
                status = "ON ✅" if coin_enabled[coin] else "OFF ❌"
                
                buttons = []
                for c in config.COINS:
                    label = f"{c} ✅" if coin_enabled.get(c, True) else f"{c} ❌"
                    buttons.append(InlineKeyboardButton(label, callback_data=f"coin_{c}"))

                await query.edit_message_text(
                    f"🪙 `{coin}` is now *{status}*\n\nToggle coins:",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([buttons])
                )
            return # Don't refresh dashboard, stay on coin toggle

        # ── Bet Direction (ETH/SOL/XRP/ALL + UP/DOWN) ──
        elif data.startswith("bet_"):
            parts     = data.split("_")
            coin_arg  = parts[1]
            direction = parts[2]
            coins_to_bet = config.COINS if coin_arg == "ALL" else [coin_arg]

            lines = []
            for coin in coins_to_bet:
                if coin not in config.COINS:
                    continue
                s   = states[coin]
                amt = s.next_bet_amount()
                lines.append(f"🎯 *{coin} {direction}* — `${amt:.0f}` (Step {s.step})")

            confirm_text = "\n".join(lines) + "\n\nConfirm?"
            # For ALL, use first coin's confirm (multi-confirm)
            first_coin = coins_to_bet[0]
            s   = states[first_coin]
            amt = s.next_bet_amount()

            if coin_arg == "ALL":
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Confirm All", callback_data=f"confirm_ALL_{direction}_{amt}"),
                    InlineKeyboardButton("❌ Cancel",      callback_data="cancel_bet"),
                ]])
            else:
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Confirm", callback_data=f"confirm_{first_coin}_{direction}_{amt}"),
                    InlineKeyboardButton("❌ Cancel",  callback_data="cancel_bet"),
                ]])

            await query.edit_message_text(confirm_text, parse_mode="Markdown", reply_markup=keyboard)
            return # Don't refresh dashboard, stay on bet confirmation

        # ── Bet Confirm ──
        elif data.startswith("confirm_"):
            parts     = data.split("_")
            coin_arg  = parts[1]
            direction = parts[2]
            coins_to_bet = config.COINS if coin_arg == "ALL" else [coin_arg]

            results = []
            for coin in coins_to_bet:
                if coin not in config.COINS:
                    continue
                s   = states[coin]
                amt = s.next_bet_amount()
                result = place_bet(coin, direction)
                if result:
                    results.append(f"✅ {coin} {direction} `${amt:.0f}` placed!")
                else:
                    results.append(f"❌ {coin} bet failed")

            await query.edit_message_text("\n".join(results), parse_mode="Markdown", reply_markup=kb_dashboard())
            return # Refresh dashboard after bet
        
        # ── Cancel ──
        elif data == "cancel_bet":
            await query.edit_message_text("❌ *Bet cancelled.*", parse_mode="Markdown", reply_markup=kb_dashboard())
            return # Refresh dashboard after cancel

    except Exception as e:
        print(f"[Telegram] Callback Error: {e}")
        try:
            await update.callback_query.answer(f"❌ Error: {e}", show_alert=True)
        except:
            pass

    # Silent dashboard update
    return


# ══════════════════════════════════════════════════════════
# ── NOTIFICATION HELPERS ─────────────────────────────────
# ══════════════════════════════════════════════════════════

async def notify_bet_placed(coin: str, direction: str, amount: float, step: int):
    # Silent update
    await update_dashboard(f"🎯 *{coin}* bet placed: `${amount:.0f}` {direction}")


async def notify_win(coin: str, amount: float, pnl: float):
    # Silent update
    await update_dashboard(f"✅ *{coin} WIN!* +`${pnl:.2f}`")


async def notify_loss(coin: str, amount: float):
    s = states[coin]
    if s.stopped:
        await update_dashboard(f"🛑 *{coin}* 7-step limit! STOPPED")
    else:
        await update_dashboard(f"❌ *{coin} LOSS!* -`${amount:.2f}`")


async def notify_daily_summary():
    total = sum(states[c].session_pnl for c in config.COINS)
    await update_dashboard(f"📊 *Daily Summary:* Net `${total:.2f}`")


async def notify_restart(crash_count: int):
    # This one can be a new message as it's a critical alert
    await send(f"⚠️ Bot crashed! Restarting... (Crash #{crash_count})")


async def notify_balance_low(balance: float):
    # Critical alert can also be a new message or update dashboard
    await update_dashboard(f"💰 *LOW BALANCE:* `${balance:.2f}` USDC")


# Removed auto_price_update repeating job as requested.

# ══════════════════════════════════════════════════════════
# ── START BOT ────────────────────────────────────────────
# ══════════════════════════════════════════════════════════

def start_telegram_bot():
    global _app, _bot

    async def run():
        global _app, _bot
        _app = Application.builder().token(config.TELEGRAM_TOKEN).build()
        _bot = _app.bot

        # Commands
        _app.add_handler(CommandHandler("start", cmd_start))
        _app.add_handler(CommandHandler("dash",  cmd_dashboard))
        _app.add_handler(CommandHandler("dashboard", cmd_dashboard))
        _app.add_handler(CommandHandler("stop",       cmd_stop))
        _app.add_handler(CommandHandler("pause",      cmd_pause))
        _app.add_handler(CommandHandler("resume",     cmd_resume))
        _app.add_handler(CommandHandler("status",     cmd_status))
        _app.add_handler(CommandHandler("balance",    cmd_balance))
        _app.add_handler(CommandHandler("price",      cmd_price))
        _app.add_handler(CommandHandler("history",    cmd_history))
        _app.add_handler(CommandHandler("martingale", cmd_martingale))
        _app.add_handler(CommandHandler("bet",        cmd_bet))
        _app.add_handler(CommandHandler("setbet",     cmd_setbet))
        _app.add_handler(CommandHandler("setstop",    cmd_setstop))
        _app.add_handler(CommandHandler("coin",       cmd_coin))
        _app.add_handler(CommandHandler("mode",       cmd_mode))
        _app.add_handler(CommandHandler("reset",      cmd_reset_all))

        # Inline button handler
        _app.add_handler(CallbackQueryHandler(handle_buttons))

        # Global error handler
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            print(f"[Telegram] Global Error: {context.error}")
            # Silently handle common network errors
            if "Query is too old" in str(context.error) or "Message is not modified" in str(context.error):
                return
            
        _app.add_error_handler(error_handler)

        print("[Telegram] Dashboard Bot started ✅")
        await _app.initialize()
        await _app.start()

        # Remove the 'Menu' button by deleting commands
        try:
            await _bot.delete_my_commands()
        except:
            pass

        await _app.updater.start_polling()
        await asyncio.Event().wait()

    def start_loop():
        global _loop
        _loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_loop)
        _loop.run_until_complete(run())

    threading.Thread(target=start_loop, daemon=True).start()
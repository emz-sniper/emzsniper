"""EMZ SNIPER — automatic paper-trading Telegram bot.
SIMULATION ONLY: never places real orders and never requests exchange API keys.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_CHAT_ID = os.getenv("OWNER_CHAT_ID", "").strip()
START_BALANCE_RON = float(os.getenv("PAPER_START_RON", "100"))
RON_PER_USDT = float(os.getenv("RON_PER_USDT", "4.6"))
STATE_FILE = Path(os.getenv("STATE_FILE", "paper_state.json"))
FEE_RATE = 0.001
COINGECKO_API = "https://api.coingecko.com/api/v3/simple/price"
MARKET_CHART_API = "https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
AUTO_SYMBOLS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
TRADE_SIZE_RON = 5.0
MAX_TRADES_PER_DAY = 3
STOP_LOSS = 0.15
TAKE_PROFIT = 0.30
SCAN_SECONDS = 900  # 15 minutes
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("emzsniper")


def load_state():
    default = {"cash_usdt": START_BALANCE_RON / RON_PER_USDT, "positions": {},
               "auto_enabled": True, "auto_position": None, "trades_today": 0,
               "trade_date": datetime.now(timezone.utc).date().isoformat()}
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if "cash_usdt" in data and "positions" in data:
                for key, value in default.items():
                    data.setdefault(key, value)
                return data
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read saved paper portfolio")
    return default


state = load_state()


def save_state():
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def allowed(update: Update) -> bool:
    return bool(update.effective_chat and OWNER_CHAT_ID and str(update.effective_chat.id) == OWNER_CHAT_ID)


async def guard(update: Update) -> bool:
    if not OWNER_CHAT_ID:
        await update.effective_message.reply_text("Configurează OWNER_CHAT_ID înainte să folosești botul.")
        return False
    if not allowed(update):
        await update.effective_message.reply_text("Acces neautorizat.")
        return False
    return True


def price(symbol: str) -> float:
    symbol = symbol.upper().replace("USDT", "")
    coin_id = AUTO_SYMBOLS.get(symbol)
    if not coin_id:
        raise ValueError(f"Monedă nesuportată de strategia automată: {symbol}")
    response = requests.get(COINGECKO_API, params={"ids": coin_id, "vs_currencies": "usd"},
                            headers={"accept": "application/json", "User-Agent": "EMZ-SNIPER/1.0"}, timeout=15)
    response.raise_for_status()
    value = response.json().get(coin_id, {}).get("usd")
    if value is None:
        raise ValueError(f"Preț indisponibil pentru {symbol}")
    return float(value)


def market_rsi(symbol: str):
    coin_id = AUTO_SYMBOLS[symbol]
    response = requests.get(MARKET_CHART_API.format(coin_id=coin_id),
                            params={"vs_currency": "usd", "days": "1"},
                            headers={"accept": "application/json", "User-Agent": "EMZ-SNIPER/1.0"}, timeout=20)
    response.raise_for_status()
    values = [float(item[1]) for item in response.json().get("prices", [])]
    if len(values) < 15:
        raise ValueError("Istoric insuficient pentru RSI")
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(change, 0) for change in changes[-14:]]
    losses = [max(-change, 0) for change in changes[-14:]]
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    if avg_loss == 0:
        rsi = 100.0 if avg_gain else 50.0
    else:
        rsi = 100 - (100 / (1 + avg_gain / avg_loss))
    return values[-1], rsi


def reset_daily_counter():
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("trade_date") != today:
        state["trade_date"] = today
        state["trades_today"] = 0


async def notify(app, message):
    try:
        await app.bot.send_message(chat_id=int(OWNER_CHAT_ID), text=message)
    except Exception:
        log.exception("Telegram notification failed")


async def auto_loop(app):
    await asyncio.sleep(5)
    while True:
        try:
            if state.get("auto_enabled", True):
                reset_daily_counter()
                position = state.get("auto_position")
                if position:
                    symbol = position["symbol"]
                    current, rsi = await asyncio.to_thread(market_rsi, symbol)
                    entry = float(position["entry_price"])
                    change = current / entry - 1
                    reason = None
                    if change <= -STOP_LOSS:
                        reason = "STOP-LOSS (-15%)"
                    elif change >= TAKE_PROFIT:
                        reason = "TAKE-PROFIT (+30%)"
                    elif rsi >= 65:
                        reason = "RSI a depășit 65"
                    if reason:
                        proceeds = float(position["qty"]) * current * (1 - FEE_RATE)
                        state["cash_usdt"] += proceeds
                        state["auto_position"] = None
                        state["trades_today"] = state.get("trades_today", 0) + 1
                        save_state()
                        await notify(app, f"EMZ SNIPER — VÂNZARE SIMULATĂ\n{symbol}\nMotiv: {reason}\nRezultat față de intrare: {change * 100:+.2f}%\nSold actualizat.")
                elif state.get("trades_today", 0) < MAX_TRADES_PER_DAY and not state.get("positions"):
                    # Buy only on a strong oversold reading; one open position maximum.
                    candidates = []
                    for symbol in AUTO_SYMBOLS:
                        try:
                            current, rsi = await asyncio.to_thread(market_rsi, symbol)
                            candidates.append((rsi, symbol, current))
                        except Exception as exc:
                            log.warning("Market scan failed for %s: %s", symbol, exc)
                    candidates.sort()
                    if candidates and candidates[0][0] <= 30:
                        rsi, symbol, current = candidates[0]
                        spend = min(TRADE_SIZE_RON / RON_PER_USDT, state["cash_usdt"])
                        if spend >= 1.0 / RON_PER_USDT:
                            qty = spend * (1 - FEE_RATE) / current
                            state["cash_usdt"] -= spend
                            state["auto_position"] = {"symbol": symbol, "qty": qty, "entry_price": current}
                            state["trades_today"] = state.get("trades_today", 0) + 1
                            save_state()
                            await notify(app, f"EMZ SNIPER — CUMPĂRARE SIMULATĂ\n{symbol}\nSumă: {spend * RON_PER_USDT:.2f} RON\nRSI: {rsi:.1f}\nStrategie: supravândut (RSI ≤ 30).")
            await asyncio.sleep(SCAN_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Automatic scan error")
            await asyncio.sleep(SCAN_SECONDS)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        "EMZ SNIPER este pornit — SIMULARE AUTOMATĂ.\n"
        "Analizează BTC, ETH și SOL la fiecare 15 minute.\n"
        "Comenzi: /portfolio, /status, /auto_on, /auto_off, /price BTC, /buy BTC 25, /sell BTC 0.0001.\n"
        "Nu execută tranzacții reale. Strategia poate pierde bani chiar și în simulare."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        "Comenzi: /status — starea automatizării; /auto_on — pornește; /auto_off — oprește; "
        "/price BTC; /buy BTC 25; /sell BTC 0.0001; /portfolio. Tranzacțiile sunt doar simulate."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    pos = state.get("auto_position")
    position_text = f"{pos['symbol']} ({pos['qty']:.8f})" if pos else "niciuna"
    reset_daily_counter()
    await update.effective_message.reply_text(
        f"Automatizare: {'PORNITĂ' if state.get('auto_enabled') else 'OPRITĂ'}\n"
        f"Poziție automată: {position_text}\nTranzacții azi: {state.get('trades_today', 0)}/{MAX_TRADES_PER_DAY}\n"
        "Scanare: la 15 minute | Mod: SIMULARE"
    )


async def auto_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    state["auto_enabled"] = True
    save_state()
    await update.effective_message.reply_text("Automatizarea este PORNITĂ în simulare.")


async def auto_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    state["auto_enabled"] = False
    save_state()
    await update.effective_message.reply_text("Automatizarea este OPRITĂ. Poziția virtuală rămâne deschisă.")


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) != 1 or context.args[0].upper().replace("USDT", "") not in AUTO_SYMBOLS:
        await update.effective_message.reply_text("Folosește: /price BTC (BTC, ETH sau SOL)")
        return
    try:
        symbol = context.args[0].upper().replace("USDT", "")
        p = await asyncio.to_thread(price, symbol)
        await update.effective_message.reply_text(f"{symbol}: {p:,.2f} USD | Mod simulare")
    except Exception as exc:
        await update.effective_message.reply_text(f"Preț indisponibil: {exc}")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Folosește: /buy BTC 25 (doar simulare)")
        return
    try:
        symbol, ron_text = context.args
        symbol = symbol.upper().replace("USDT", "")
        if symbol not in AUTO_SYMBOLS:
            raise ValueError("Sunt disponibile BTC, ETH și SOL.")
        ron = float(ron_text)
        spend = ron / RON_PER_USDT
        p = await asyncio.to_thread(price, symbol)
        if ron <= 0 or spend > state["cash_usdt"]:
            raise ValueError("Sumă invalidă sau sold insuficient.")
        qty = spend * (1 - FEE_RATE) / p
        state["cash_usdt"] -= spend
        state["positions"][symbol] = state["positions"].get(symbol, 0.0) + qty
        save_state()
        await update.effective_message.reply_text(f"SIMULARE CUMPĂRARE: {qty:.8f} {symbol}, cost {ron:.2f} RON.")
    except Exception as exc:
        await update.effective_message.reply_text(f"Nu s-a efectuat simularea: {exc}")


async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Folosește: /sell BTC 0.0001")
        return
    try:
        symbol, qty_text = context.args
        symbol = symbol.upper().replace("USDT", "")
        qty = float(qty_text)
        held = state["positions"].get(symbol, 0.0)
        if qty <= 0 or qty > held:
            raise ValueError(f"Deții {held:.8f} {symbol} în portofoliul manual.")
        p = await asyncio.to_thread(price, symbol)
        proceeds = qty * p * (1 - FEE_RATE)
        state["cash_usdt"] += proceeds
        state["positions"][symbol] = held - qty
        if state["positions"][symbol] < 1e-12:
            del state["positions"][symbol]
        save_state()
        await update.effective_message.reply_text(f"SIMULARE VÂNZARE: {qty:.8f} {symbol}, încasare {proceeds * RON_PER_USDT:.2f} RON.")
    except Exception as exc:
        await update.effective_message.reply_text(f"Nu s-a efectuat simularea: {exc}")


async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    cash = state["cash_usdt"]
    total = cash
    lines = [f"Sold disponibil: {cash * RON_PER_USDT:.2f} RON"]
    for symbol, qty in state["positions"].items():
        try:
            value = qty * await asyncio.to_thread(price, symbol)
            total += value
            lines.append(f"{symbol} manual: {qty:.8f} ≈ {value * RON_PER_USDT:.2f} RON")
        except Exception:
            lines.append(f"{symbol}: {qty:.8f} (preț indisponibil)")
    pos = state.get("auto_position")
    if pos:
        try:
            value = pos["qty"] * await asyncio.to_thread(price, pos["symbol"])
            total += value
            lines.append(f"{pos['symbol']} automat: {pos['qty']:.8f} ≈ {value * RON_PER_USDT:.2f} RON")
        except Exception:
            lines.append(f"{pos['symbol']} automat: preț indisponibil")
    lines.append(f"Valoare totală estimată: {total * RON_PER_USDT:.2f} RON")
    lines.append("SIMULARE — rezultatele nu garantează profit.")
    await update.effective_message.reply_text("\n".join(lines))


async def post_init(app: Application):
    app.create_task(auto_loop(app))


def main():
    if not TOKEN:
        raise SystemExit("Lipsește TELEGRAM_BOT_TOKEN din secrets.")
    app = Application.builder().token(TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("auto_on", auto_on))
    app.add_handler(CommandHandler("auto_off", auto_off))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("portfolio", portfolio))
    log.info("EMZ SNIPER started in PAPER mode")
    app.run_polling()


if __name__ == "__main__":
    main()

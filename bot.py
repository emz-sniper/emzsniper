"""EMZ SNIPER — educational paper-trading Telegram bot.
SIMULATION ONLY: this bot never places real orders or requests exchange API keys.
"""
import json
import logging
import os
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
API = "https://api.binance.com/api/v3/ticker/price"
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("emzsniper")


def load_state():
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if "cash_usdt" in data and "positions" in data:
                return data
        except (OSError, json.JSONDecodeError):
            log.exception("Could not read saved paper portfolio")
    return {"cash_usdt": START_BALANCE_RON / RON_PER_USDT, "positions": {}}


state = load_state()


def save_state():
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def allowed(update: Update) -> bool:
    return bool(update.effective_chat and OWNER_CHAT_ID and str(update.effective_chat.id) == OWNER_CHAT_ID)


async def guard(update: Update) -> bool:
    if not OWNER_CHAT_ID:
        await update.effective_message.reply_text("Configurează OWNER_CHAT_ID în fișierul .env înainte să folosești botul.")
        return False
    if not allowed(update):
        await update.effective_message.reply_text("Acces neautorizat.")
        return False
    return True


def price(symbol: str) -> float:
    symbol = symbol.upper()
    if not symbol.endswith("USDT"):
        symbol += "USDT"
    if not symbol[:-4].isalnum() or len(symbol) > 20:
        raise ValueError("Simbol invalid. Exemplu: BTC sau ETH.")
    response = requests.get(API, params={"symbol": symbol}, timeout=10)
    response.raise_for_status()
    return float(response.json()["price"])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not OWNER_CHAT_ID:
        await update.effective_message.reply_text(
            f"ID-ul conversației tale este: {update.effective_chat.id}\\n"
            "Adaugă acest număr ca OWNER_CHAT_ID în .env, apoi repornește botul."
        )
        return
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        "EMZ SNIPER este pornit — MOD SIMULARE בלבד.\n"
        "Comenzi:\n/price BTC — preț orientativ\n"
        "/buy BTC 25 — cumpără simulativ de 25 RON\n"
        "/sell BTC 0.0001 — vinde cantitatea indicată\n"
        "/portfolio — portofoliu și sold\n"
        "/help — ajutor\n\n"
        "Nu se execută tranzacții reale."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.effective_message.reply_text(
        "Exemple: /price BTC, /buy BTC 25, /sell BTC 0.0001, /portfolio. "
        "Sumele pentru buy sunt în RON; cantitățile pentru sell sunt în monede."
    )


async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) != 1:
        await update.effective_message.reply_text("Folosește: /price BTC")
        return
    try:
        p = price(context.args[0])
        await update.effective_message.reply_text(f"{context.args[0].upper()}/USDT: {p:,.8f} USDT\nMod simulare.")
    except Exception as exc:
        await update.effective_message.reply_text(f"Nu am putut obține prețul: {exc}")


async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    if len(context.args) != 2:
        await update.effective_message.reply_text("Folosește: /buy BTC 25 (25 RON)")
        return
    try:
        symbol, ron_text = context.args
        ron = float(ron_text)
        if ron <= 0:
            raise ValueError("Suma trebuie să fie pozitivă.")
        spend = ron / RON_PER_USDT
        p = price(symbol)
        if spend > state["cash_usdt"]:
            raise ValueError("Sold insuficient.")
        qty = (spend * (1 - FEE_RATE)) / p
        key = symbol.upper().replace("USDT", "")
        state["cash_usdt"] -= spend
        state["positions"][key] = state["positions"].get(key, 0.0) + qty
        save_state()
        await update.effective_message.reply_text(
            f"SIMULARE CUMPĂRARE\n{qty:.8f} {key}\nCost: {ron:.2f} RON "
            f"(preț {p:.6f} USDT)\nComision simulat: 0,1%."
        )
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
        qty = float(qty_text)
        key = symbol.upper().replace("USDT", "")
        held = state["positions"].get(key, 0.0)
        if qty <= 0 or qty > held:
            raise ValueError(f"Deții {held:.8f} {key}.")
        p = price(key)
        proceeds = qty * p * (1 - FEE_RATE)
        state["cash_usdt"] += proceeds
        state["positions"][key] = held - qty
        if state["positions"][key] < 1e-12:
            del state["positions"][key]
        save_state()
        await update.effective_message.reply_text(
            f"SIMULARE VÂNZARE\n{qty:.8f} {key}\nÎncasare estimată: "
            f"{proceeds * RON_PER_USDT:.2f} RON\nComision simulat: 0,1%."
        )
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
            value = qty * price(symbol)
            total += value
            lines.append(f"{symbol}: {qty:.8f} ≈ {value * RON_PER_USDT:.2f} RON")
        except Exception:
            lines.append(f"{symbol}: {qty:.8f} (preț indisponibil)")
    lines.append(f"Valoare totală estimată: {total * RON_PER_USDT:.2f} RON")
    lines.append("SIMULARE — valorile sunt orientative.")
    await update.effective_message.reply_text("\n".join(lines))


def main():
    if not TOKEN:
        raise SystemExit("Lipsește TELEGRAM_BOT_TOKEN din .env")
    if not OWNER_CHAT_ID:
        raise SystemExit("Lipsește OWNER_CHAT_ID din .env")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("price", price_cmd))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("portfolio", portfolio))
    log.info("EMZ SNIPER running in PAPER mode")
    app.run_polling()


if __name__ == "__main__":
    main()

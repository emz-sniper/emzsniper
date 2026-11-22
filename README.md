# EMZ SNIPER

Telegram-controlled **paper-trading simulator**. It reads public Binance ticker prices and simulates trades locally. It never sends real orders and does not need exchange API keys.

## Setup
1. Create a Telegram bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. On the computer/server where you will run the bot, copy `.env.example` to `.env` and enter the token.
3. Install Python 3.11+ and run `pip install -r requirements.txt`.
4. Start with `python bot.py`, then send `/start` to your bot. It will show your chat ID during first setup.
5. Put that number in `OWNER_CHAT_ID` in `.env`, restart the bot, then use it privately.

## Commands
- `/price BTC` — public market price
- `/buy BTC 25` — simulate buying with 25 RON
- `/sell BTC 0.0001` — simulate selling an asset quantity
- `/portfolio` — paper cash and holdings
- `/help` — command help

Starting balance is 100 RON (converted to USDT using the configurable approximate `RON_PER_USDT`, default 4.6). Simulated fee is 0.1%. Portfolio state is stored in `paper_state.json`.

**Safety:** paper trading only. There is no live-trading switch or exchange API integration. Simulation is educational and does not predict future returns.

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import httpx
import matplotlib.pyplot as plt
import io

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ── CONFIG ──
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

DATA_FILE = Path("data.json")

BASE_POSITION_SIZE = 0.2
STOP_LOSS = -3
TAKE_PROFIT = 5

# ── STORAGE ──
def load():
    return json.loads(DATA_FILE.read_text()) if DATA_FILE.exists() else {}

def save(data):
    DATA_FILE.write_text(json.dumps(data, indent=2))

# ── INDICATORS ──
def rsi(prices, p=14):
    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_g = sum(gains[-p:]) / p
    avg_l = sum(losses[-p:]) / p
    if avg_l == 0:
        return 100
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def trend(prices):
    ema50 = sum(prices[-50:]) / 50
    ema200 = sum(prices[-200:]) / 200
    return "UP" if ema50 > ema200 else "DOWN"

def analyze(trades):
    if not trades:
        return {"winrate": 0}
    wins = [t for t in trades if t["pnl"] > 0]
    return {"winrate": len(wins) / len(trades) * 100}

# ── FETCH COINS ──
async def get_coins():
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "order": "volume_desc", "per_page": 20}
        )
    return [c["id"] for c in r.json()[:10]]

# ── BACKTEST CORE ──
def run_backtest(prices):
    balance = 1000
    position = None
    equity = [balance]
    trades = []

    for i in range(200, len(prices)):
        price = prices[i]
        ema50 = sum(prices[i-50:i]) / 50
        ema200 = sum(prices[i-200:i]) / 200
        rsi_val = rsi(prices[:i])

        if not position and ema50 > ema200 and rsi_val < 35:
            position = price

        elif position:
            change = (price - position) / position * 100
            if change >= TAKE_PROFIT or change <= STOP_LOSS:
                balance *= (1 + change / 100)
                equity.append(balance)
                trades.append(change)
                position = None

    return balance, trades, equity

# ── COMMANDS ──
async def start(update: Update, context):
    await update.message.reply_text("🚀 Bot listo\n/test 1000")

async def test(update: Update, context):
    user = str(update.effective_user.id)
    amount = float(context.args[0])

    data = load()
    data[user] = {
        "cash": amount,
        "start": amount,
        "auto": False,
        "positions": {},
        "trades": [],
        "position_size": BASE_POSITION_SIZE
    }
    save(data)

    await update.message.reply_text(f"💰 Portfolio ${amount}")

async def autostart(update: Update, context):
    user = str(update.effective_user.id)
    data = load()
    data[user]["auto"] = True
    save(data)
    await update.message.reply_text("🤖 AUTO ON")

async def autostop(update: Update, context):
    user = str(update.effective_user.id)
    data = load()
    data[user]["auto"] = False
    save(data)
    await update.message.reply_text("⛔ AUTO OFF")

async def portfolio(update: Update, context):
    user = str(update.effective_user.id)
    p = load()[user]

    equity = p["cash"]
    for pos in p["positions"].values():
        equity += pos["qty"] * pos["entry"]

    pnl = equity - p["start"]

    await update.message.reply_text(
        f"💼 Equity: ${equity:.2f}\nPnL: ${pnl:.2f}"
    )

async def stats(update: Update, context):
    user = str(update.effective_user.id)
    trades = load()[user]["trades"]
    s = analyze(trades)

    await update.message.reply_text(
        f"📊 Winrate: {s['winrate']:.1f}%\nTrades: {len(trades)}"
    )

async def pnlchart(update: Update, context):
    user = str(update.effective_user.id)
    trades = load()[user]["trades"]

    equity = [0]
    total = 0
    for t in trades:
        total += t["pnl"]
        equity.append(total)

    plt.plot(equity)
    plt.title("Live PnL")

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)

    await update.message.reply_photo(buf)

async def backtest(update: Update, context):
    await update.message.reply_text("⏳ Running backtest...")

    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": 30}
        )
        prices = [p[1] for p in r.json()["prices"]]

    balance, trades, _ = run_backtest(prices)

    winrate = len([t for t in trades if t > 0]) / len(trades) * 100 if trades else 0

    await update.message.reply_text(
        f"Start: $1000\nEnd: ${balance:.2f}\nTrades: {len(trades)}\nWinrate: {winrate:.1f}%"
    )

async def backchart(update: Update, context):
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": 30}
        )
        prices = [p[1] for p in r.json()["prices"]]

    _, _, equity = run_backtest(prices)

    plt.plot(equity)
    plt.title("Backtest Equity")

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)

    await update.message.reply_photo(buf)

# ── AUTO LOOP ──
async def auto_loop(app):
    while True:
        await asyncio.sleep(120)
        data = load()
        coins = await get_coins()

        async with httpx.AsyncClient(timeout=10) as client:
            for coin in coins:
                try:
                    r = await client.get(
                        f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart",
                        params={"vs_currency": "usd", "days": 1}
                    )
                    prices = [p[1] for p in r.json()["prices"]]
                except:
                    continue

                price = prices[-1]
                rsi_val = rsi(prices)
                t = trend(prices)

                for user, p in data.items():
                    if not p["auto"]:
                        continue

                    pos = p["positions"].get(coin)
                    size = p["position_size"]

                    if not pos and rsi_val < 35 and t == "UP":
                        amount = p["cash"] * size
                        qty = amount / price

                        p["positions"][coin] = {"entry": price, "qty": qty}
                        p["cash"] -= amount

                        await app.bot.send_message(int(user), f"🟢 BUY {coin}")

                    elif pos:
                        change = (price - pos["entry"]) / pos["entry"] * 100

                        if change >= TAKE_PROFIT or change <= STOP_LOSS:
                            p["cash"] += pos["qty"] * price
                            p["trades"].append({"coin": coin, "pnl": change})
                            del p["positions"][coin]

                            await app.bot.send_message(int(user), f"🔴 SELL {coin} {change:.2f}%")

                    if len(p["trades"]) > 5:
                        winrate = analyze(p["trades"])["winrate"]

                        if winrate < 40:
                            p["position_size"] = 0.1
                        elif winrate > 60:
                            p["position_size"] = 0.25
                        else:
                            p["position_size"] = BASE_POSITION_SIZE

        save(data)

# ── MAIN ──
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("autostart", autostart))
    app.add_handler(CommandHandler("autostop", autostop))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("pnlchart", pnlchart))
    app.add_handler(CommandHandler("backtest", backtest))
    app.add_handler(CommandHandler("backchart", backchart))

    async def post_init(app):
        asyncio.create_task(auto_loop(app))

    app.post_init = post_init

    print("🔥 BOT FULL RUNNING")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

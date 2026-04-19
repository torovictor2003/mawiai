import asyncio
import json
import logging
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

COINS = ["bitcoin", "ethereum", "solana"]

STOP_LOSS = -2.5
TAKE_PROFIT = 5
POSITION_SIZE = 0.2

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FILE = Path("data.json")

# ── STORAGE ──
def load():
    return json.loads(FILE.read_text()) if FILE.exists() else {}

def save(d):
    FILE.write_text(json.dumps(d, indent=2))

# ── INDICADORES ──
def rsi(prices, p=14):
    gains, losses = [], []
    for i in range(1, len(prices)):
        d = prices[i] - prices[i-1]
        gains.append(max(d, 0))
        losses.append(abs(min(d, 0)))
    avg_g = sum(gains[-p:]) / p
    avg_l = sum(losses[-p:]) / p
    if avg_l == 0:
        return 100
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def detect_trend(prices):
    ema50 = sum(prices[-50:]) / 50
    ema200 = sum(prices[-200:]) / 200

    if ema50 > ema200 * 1.01:
        return "UP"
    elif ema50 < ema200 * 0.99:
        return "DOWN"
    return "SIDEWAYS"

def analyze_performance(trades):
    if not trades:
        return {"winrate": 0, "avg_win": 0, "avg_loss": 0}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    winrate = len(wins) / len(trades) * 100
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0

    return {"winrate": winrate, "avg_win": avg_win, "avg_loss": avg_loss}

# ── COMMANDS ──
async def start(update: Update, context):
    await update.message.reply_text("🚀 Bot Nivel PRO activo\n/test 1000")

async def test(update: Update, context):
    user = str(update.effective_user.id)

    if not context.args:
        await update.message.reply_text("Use /test 1000")
        return

    amount = float(context.args[0])
    d = load()

    d[user] = {
        "cash": amount,
        "start": amount,
        "auto": False,
        "positions": {},
        "trades": [],
        "trend": {},
        "last_trade_time": None
    }

    save(d)
    await update.message.reply_text(f"💰 Portfolio creado ${amount}")

async def autostart(update: Update, context):
    user = str(update.effective_user.id)
    d = load()
    d[user]["auto"] = True
    save(d)
    await update.message.reply_text("🤖 AUTO ON")

async def autostop(update: Update, context):
    user = str(update.effective_user.id)
    d = load()
    d[user]["auto"] = False
    save(d)
    await update.message.reply_text("⛔ AUTO OFF")

async def portfolio(update: Update, context):
    user = str(update.effective_user.id)
    d = load()
    p = d[user]

    equity = p["cash"]
    for c, pos in p["positions"].items():
        equity += pos["qty"] * pos["entry"]

    pnl = equity - p["start"]

    await update.message.reply_text(
        f"💼 Equity: ${equity:.2f}\nPnL: ${pnl:.2f}\nCash: ${p['cash']:.2f}"
    )

async def positions(update: Update, context):
    user = str(update.effective_user.id)
    p = load()[user]

    if not p["positions"]:
        await update.message.reply_text("No hay trades activos")
        return

    msg = "📊 POSICIONES:\n"
    for c, pos in p["positions"].items():
        msg += f"{c.upper()} @ {pos['entry']}\n"

    await update.message.reply_text(msg)

async def trends(update: Update, context):
    user = str(update.effective_user.id)
    p = load()[user]

    msg = "📡 TENDENCIAS:\n"
    for c, t in p["trend"].items():
        msg += f"{c.upper()}: {t}\n"

    await update.message.reply_text(msg)

async def stats(update: Update, context):
    user = str(update.effective_user.id)
    trades = load()[user]["trades"]

    s = analyze_performance(trades)

    await update.message.reply_text(
        f"Winrate: {s['winrate']:.1f}%\nAvgWin: {s['avg_win']:.2f}%\nAvgLoss: {s['avg_loss']:.2f}%"
    )

async def pnlchart(update: Update, context):
    user = str(update.effective_user.id)
    trades = load()[user]["trades"]

    if not trades:
        await update.message.reply_text("No trades aún")
        return

    equity = [0]
    total = 0

    for t in trades:
        total += t["pnl"]
        equity.append(total)

    plt.plot(equity)
    plt.title("Equity Curve")

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)

    await update.message.reply_photo(photo=buf)

# ── AUTO LOOP ──
async def auto_loop(app):
    global POSITION_SIZE

    while True:
        await asyncio.sleep(90)
        d = load()

        async with httpx.AsyncClient() as client:
            for coin in COINS:
                r = await client.get(
                    f"https://api.coingecko.com/api/v3/coins/{coin}/market_chart?vs_currency=usd&days=1"
                )
                prices = [p[1] for p in r.json()["prices"]]

                price = prices[-1]
                trend = detect_trend(prices)
                rsi_val = rsi(prices)
                momentum = (prices[-1] - prices[-10]) / prices[-10] * 100
                volatility = (max(prices[-20:]) - min(prices[-20:])) / price * 100

                for user, p in d.items():
                    if not p.get("auto"):
                        continue

                    # trend alert
                    if coin not in p["trend"] or p["trend"][coin] != trend:
                        p["trend"][coin] = trend
                        await app.bot.send_message(
                            chat_id=int(user),
                            text=f"📡 TREND {coin.upper()} → {trend}"
                        )

                    pos = p["positions"].get(coin)

                    # avoid dead market
                    if volatility < 1:
                        continue

                    # avoid spam trading
                    if p["last_trade_time"]:
                        diff = (datetime.now(timezone.utc) - datetime.fromisoformat(p["last_trade_time"])).seconds
                        if diff < 300:
                            continue

                    # BUY
                    if not pos and trend == "UP" and rsi_val < 40 and momentum > 0:
                        amount = p["cash"] * POSITION_SIZE
                        qty = amount / price

                        p["positions"][coin] = {
                            "entry": price,
                            "qty": qty,
                            "peak": price
                        }

                        p["cash"] -= amount
                        p["last_trade_time"] = datetime.now(timezone.utc).isoformat()

                        await app.bot.send_message(
                            chat_id=int(user),
                            text=f"🟢 BUY {coin.upper()} RSI:{rsi_val:.1f}"
                        )

                    # SELL
                    elif pos:
                        if price > pos["peak"]:
                            pos["peak"] = price

                        drawdown = (price - pos["peak"]) / pos["peak"] * 100
                        profit = (price - pos["entry"]) / pos["entry"] * 100

                        if profit >= TAKE_PROFIT or drawdown <= STOP_LOSS or rsi_val > 70:
                            p["cash"] += pos["qty"] * price

                            p["trades"].append({
                                "coin": coin,
                                "pnl": profit,
                                "time": datetime.now(timezone.utc).isoformat()
                            })

                            p["positions"].pop(coin)
                            p["last_trade_time"] = datetime.now(timezone.utc).isoformat()

                            await app.bot.send_message(
                                chat_id=int(user),
                                text=f"🔴 SELL {coin.upper()} PnL {profit:.2f}%"
                            )

                    # adaptive risk
                    if len(p["trades"]) > 10:
                        perf = analyze_performance(p["trades"])
                        if perf["winrate"] < 40:
                            POSITION_SIZE = 0.1
                        elif perf["winrate"] > 60:
                            POSITION_SIZE = 0.25

        save(d)

# ── MAIN ──
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("test", test))
    app.add_handler(CommandHandler("autostart", autostart))
    app.add_handler(CommandHandler("autostop", autostop))
    app.add_handler(CommandHandler("portfolio", portfolio))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("trends", trends))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("pnlchart", pnlchart))

    async def post_init(app):
        asyncio.create_task(auto_loop(app))

    app.post_init = post_init

    print("🔥 BOT NIVEL DIOS RUNNING")
    app.run_polling()

if __name__ == "__main__":
    main()
